# -*- coding: utf-8 -*-
"""
扫描件脱敏工具
==============
把真实凭证 PDF 里的公司名、人名（可选：水印）涂白并替换为虚构值，
输出一份可以公开的版本。适用于**无文字层的扫描件**——此时不能改文本层，
只能按 OCR 定位坐标，在页面上覆盖白块再写回替代文字。

用法：
    python -m tools.desensitize 输入.pdf 输出.pdf
    python -m tools.desensitize 输入.pdf 输出.pdf --rules 宏亿=示例精工,陶鑫辉=张三
    python -m tools.desensitize 输入.pdf 输出.pdf --no-watermark   # 保留水印

注意：
    · 只处理**文字**。二维码、印章、装订孔等图像元素不动。
    · **金额默认不动**。金额不是个人隐私；若确需改金额，必须同步重算合计
      与大写金额，否则凭证内部自相矛盾，失去演示意义。
    · 涂白依赖 OCR 定位。OCR 没认出来的文字，坐标也定位不到，因此无法遮盖。
      处理完务必逐页目视复核，这是金融/审计场景的基本要求。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# 默认替换规则：命中即涂白并改写
DEFAULT_RULES: list[tuple[str, str]] = [
    ("江苏宏亿精工股份有限公司", "示例精工股份有限公司"),
    ("宏亿", "示例"), ("宏忆", "示例"), ("宏亿", "示例"),
    # 人名：OCR 每页认的字可能都不一样，这里只是兜底，主力靠下面的锚点遮盖
    ("陶鑫辉", "张三"), ("陶铭辉", "张三"),
    ("王崧坡", "李四"), ("王数披", "李四"), ("王兹披", "李四"),
    ("扫描全能王", ""),
    ("3亿人都在用的扫描App", ""),
    ("3 亿人都在用的扫描 App", ""),
]

# 锚点遮盖：(锚点文本, 替换文本, 覆盖字数)
# 中文姓名 OCR 极不稳定（同一页不同次识别结果都不同），靠匹配姓名本身必然漏。
# 改为锚定旁边的固定标签，把标签右侧一块区域整体涂掉 —— 标签的识别率远高于人名。
# 锚点优先用英文（checked by / produced by / unit），英文 OCR 比中文稳。
DEFAULT_ANCHORS: list[tuple[str, str, float]] = [
    ("che", "张三", 5.6),      # 含 "by" + 3 字姓名，checked/cheoked/cheeked 均可前缀命中
    ("prod", "李四", 5.6),     # 含 "by" + 3 字姓名，produced/producced
    ("unit", "示例精工股份有限公司", 13.8),
]


def _span_words(words: list[dict], needle: str) -> list[dict] | None:
    """在一行内定位子串，返回覆盖到的词（去空白后匹配，规避 OCR 分词）。"""
    needle_ns = "".join(needle.split()).lower()
    if not needle_ns:
        return None
    chars: list[tuple[str, int]] = []
    for wi, w in enumerate(words):
        for ch in w["text"].lower():
            if not ch.isspace():
                chars.append((ch, wi))
    joined = "".join(c for c, _ in chars)
    idx = joined.find(needle_ns)
    if idx < 0:
        return None
    end = idx + len(needle_ns)
    idxs = sorted({wi for _, wi in chars[idx:end]})
    return [words[i] for i in idxs] or None


def _anchor_box(words: list[dict], needle: str, n_chars: float,
                img_w: int) -> tuple[float, float, float, float] | None:
    """锚点右侧区域：锚点词的右边界起，向右扩 n_chars 个字高。"""
    hit = _span_words(words, needle)
    if not hit:
        # 前缀兜底：OCR 会把 checked 认成 cheoked/cheeked，把 produced 认成 producced。
        # 这类英文标签首字母几乎从不错，用前缀比整词匹配稳得多。
        cands = [w for w in words
                 if w["text"].lower().strip().startswith(needle)
                 and sum(c.isalpha() for c in w["text"]) >= 3]
        hit = cands[:1] if cands else None
    if not hit:
        return None
    x1 = max(w["left"] + w["width"] for w in hit)
    # 垂直范围取整行而非锚点词本身：英文标签的 bbox 比中文矮，
    # 若按锚点词高算字号，替换出来的中文会明显偏小。
    y0 = min(w["top"] for w in words)
    y1 = max(w["top"] + w["height"] for w in words)
    h = max(1.0, y1 - y0)
    x2 = min(x1 + n_chars * h * 0.95, img_w - 1)
    return (x1 + h * 0.15, y0, x2, y1)


def _label_row_block(all_words: list[dict], label: str,
                     img_w: int) -> tuple[float, float, float, float] | None:
    """
    定位「复核人 / 制单人」标签右侧、同一水平带内的中文块。

    不能在单个 OCR 行内找：同一行文字常被切成多个 line_num
    （「复核人 Checked by」和「陶鑫辉」在不同行），按行找就漏。
    改为在全页范围按 y 中心 ±1.3 个字高筛同带候选，再取最右侧的中文词。
    """
    import re as _re
    labs = [w for w in all_words if label in "".join(w["text"].split())]
    if not labs:
        return None
    lab = min(labs, key=lambda x: (x["top"], x["left"]))
    lh = max(1.0, lab["height"])
    ly = lab["top"] + lh / 2
    lx1 = lab["left"] + lab["width"]

    cands = []
    for w in all_words:
        if w is lab:
            continue
        txt = "".join(w["text"].split())
        if not _re.search(r"[\u4e00-\u9fff]", txt):
            continue
        if label in txt:                      # 别把标签本身当候选
            continue
        wy = w["top"] + w["height"] / 2
        if abs(wy - ly) > lh * 1.3:           # 只取同一水平带
            continue
        if w["left"] < lx1 - lh * 2:          # 必须在标签右侧
            continue
        cands.append(w)
    if not cands:
        return None

    w = max(cands, key=lambda x: x["left"])
    y0 = min(w["top"], lab["top"])
    y1 = max(w["top"] + w["height"], lab["top"] + lab["height"])
    h = max(1.0, y1 - y0)
    x0 = max(0.0, w["left"] - h * 0.3)
    x1 = min(w["left"] + 3.6 * h, img_w - 1)
    return (x0, y0, x1, y1)


# 标签 → (替换文本, 该行标签词本身，用于排除)
LABEL_ROWS: list[tuple[str, str]] = [
    ("复核人", "张三"),
    ("制单人", "李四"),
]


def _bg_color(img: Image.Image, box: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """
    取该区域的底色，用于填充遮盖块。

    不能直接取中位数：姓名栏这类位置文字又黑又密，中位数会落到墨色上，
    涂出来就是一个黑块。改为取亮度最高的 30% 像素的中位色 —— 文字总比
    纸面暗，最亮的那部分一定属于背景。
    """
    import numpy as np

    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return (1.0, 1.0, 1.0)

    def _med(arr):
        arr = np.asarray(arr).reshape(-1, 3).astype(float)
        if arr.size == 0:
            return None
        m = np.median(arr, axis=0) / 255.0
        return (float(m[0]), float(m[1]), float(m[2]))

    # 优先取框外上下相邻的窄带 —— 那里一定是纯纸面，不带文字
    band = max(2, int((y1 - y0) * 0.3))
    strips = []
    if y0 - band >= 0:
        strips.append(img.crop((x0, y0 - band, x1, y0)))
    if y1 + band <= img.height:
        strips.append(img.crop((x0, y1, x1, y1 + band)))
    if strips:
        total = sum(s.width * s.height for s in strips)
        if total > 0:
            merged = Image.new("RGB", (max(s.width for s in strips), sum(s.height for s in strips)))
            yy = 0
            for s in strips:
                merged.paste(s.convert("RGB"), (0, yy))
                yy += s.height
            c = _med(merged)
            if c:
                return c

    # 回退：框内亮度最高的 30%（文字总比纸面暗）
    arr = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB")).reshape(-1, 3).astype(float)
    if arr.size == 0:
        return (1.0, 1.0, 1.0)
    lum = arr.mean(axis=1)
    bright = arr[lum >= np.percentile(lum, 70)]
    if bright.size == 0:
        bright = arr
    c = _med(bright)
    return c if c else (1.0, 1.0, 1.0)


def _group_lines(img: Image.Image, psm: int = 4) -> list[list[dict]]:
    """OCR 后按「块/段/行」归组，得到每行的词及其像素 bbox。"""
    data = pytesseract.image_to_data(
        img, lang="chi_sim", config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    lines: dict[tuple, list[dict]] = {}
    for i, raw in enumerate(data["text"]):
        txt = (raw or "").strip()
        if not txt:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append({
            "text": txt,
            "left": data["left"][i], "top": data["top"][i],
            "width": data["width"][i], "height": data["height"][i],
        })
    return list(lines.values())


def _span_box(words: list[dict], needle: str) -> tuple[float, float, float, float] | None:
    """
    在一行内定位子串，返回其像素 bbox（取覆盖到的词的并集）。

    OCR 常把中文按词切开（"江苏 宏 亿 精工 股份"），直接做字符串包含会全部落空。
    因此先把整行和敏感词都去掉空白再匹配，再通过字符→词的下标映射回坐标。
    """
    needle_ns = "".join(needle.split())
    if not needle_ns:
        return None
    chars: list[tuple[str, int]] = []          # (字符, 所属词下标)
    for wi, w in enumerate(words):
        for ch in w["text"]:
            if not ch.isspace():
                chars.append((ch, wi))
    joined = "".join(c for c, _ in chars)
    idx = joined.find(needle_ns)
    if idx < 0:
        return None
    end = idx + len(needle_ns)
    hit_idx = sorted({wi for _, wi in chars[idx:end]})
    hit = [words[i] for i in hit_idx]
    if not hit:
        return None
    x0 = min(w["left"] for w in hit)
    y0 = min(w["top"] for w in hit)
    x1 = max(w["left"] + w["width"] for w in hit)
    y1 = max(w["top"] + w["height"] for w in hit)
    return x0, y0, x1, y1


def desensitize(
    src: str | Path,
    dst: str | Path,
    rules: list[tuple[str, str]] | None = None,
    anchors: list[tuple[str, str, float]] | None = None,
    dpi: int = 300,
    psm: int = 4,
    progress: bool = True,
) -> dict:
    """
    执行脱敏，返回统计：{pages, hits, by_rule}
    """
    rules = rules if rules is not None else DEFAULT_RULES
    anchors = anchors if anchors is not None else DEFAULT_ANCHORS
    # 长串优先，避免「江苏宏亿精工股份有限公司」先被「宏亿」截断
    rules = sorted(rules, key=lambda r: -len(r[0]))

    doc = fitz.open(str(src))
    stat: dict = {"pages": doc.page_count, "hits": 0, "by_rule": {}}

    for pno in range(doc.page_count):
        page = doc[pno]
        pix = page.get_pixmap(dpi=dpi)
        mode = "RGB" if pix.n == 3 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        if mode == "RGBA":
            img = img.convert("RGB")

        # 像素 → PDF 点 的换算系数
        scale = page.rect.width / pix.width
        boxes = []
        lines = _group_lines(img, psm=psm)
        all_words = [w for ln in lines for w in ln]

        # ② 标签行兜底：见到「复核人 / 制单人」，遮其右侧同带的中文块（跨行搜索）
        for label, repl in LABEL_ROWS:
            rbox = _label_row_block(all_words, label, img.width)
            if rbox is not None:
                boxes.append((rbox, f"#{label}", repl))

        for words in lines:
            # OCR 会在中文词之间插空格，比较前统一去空白
            line_text = "".join("".join(w["text"] for w in words).split())

            # ① 锚点遮盖：锚点右侧固定宽度的区域，整块替换（应对 OCR 认错人名）
            for anchor, repl, n_chars in anchors:
                abox = _anchor_box(words, anchor, n_chars, img.width)
                if abox is not None:
                    boxes.append((abox, f"@{anchor}", repl))

            # ③ 关键词遮盖
            for needle, repl in rules:
                if "".join(needle.split()) not in line_text:
                    continue
                box = _span_box(words, needle)
                if box is None:
                    continue
                boxes.append((box, needle, repl))

        # 同一位置可能被「长串」和「短串」同时命中，只保留最长的那个
        boxes.sort(key=lambda b: -(b[0][2] - b[0][0]) * (b[0][3] - b[0][1]))
        kept: list[tuple] = []
        for box, needle, repl in boxes:
            x0, y0, x1, y1 = box
            dup = False
            for kb, _, _ in kept:
                kx0, ky0, kx1, ky1 = kb
                # 中心点落在已有框内 → 视为重复
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if kx0 <= cx <= kx1 and ky0 <= cy <= ky1:
                    dup = True
                    break
            if dup:
                continue
            kept.append((box, needle, repl))

        for (x0, y0, x1, y1), needle, repl in kept:
            r = fitz.Rect(x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            # 覆盖：用该处底色填充，避免在米黄纸面上留白块
            page.draw_rect(r, color=None, fill=_bg_color(img, (x0, y0, x1, y1)), width=0)
            if repl:
                box_w = (x1 - x0) * scale
                box_h = (y1 - y0) * scale
                if len(repl) <= 4:
                    # 短替换（张三代人名）：以字高为准，右边通常是空白，允许轻微溢出
                    size = max(8.0, box_h * 0.85)
                else:
                    # 长替换（公司名）：必须压进原框宽度内，否则会盖到相邻字段
                    size = min(box_h * 0.85, box_w / len(repl) * 1.02)
                size = min(size, 16.0)
                page.insert_text(
                    (r.x0, r.y0 + size * 0.88),   # baseline 下移，使文字在框内垂直居中
                    repl, fontname="china-s", fontfile=FONT,
                    fontsize=size, color=(0, 0, 0),
                )
            stat["hits"] += 1
            stat["by_rule"][needle] = stat["by_rule"].get(needle, 0) + 1

        if progress:
            print(f"  page {pno + 1}/{doc.page_count} → 遮盖 {len(kept)} 处", flush=True)

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst), garbage=3, deflate=True)
    doc.close()
    stat["output"] = str(dst)
    stat["size_mb"] = round(dst.stat().st_size / 1048576, 2)
    return stat


# 自检用关键词：只用于「脱敏后还有没有残留」，宁可误报不可漏报
VERIFY_KEYS: list[str] = [
    "宏亿", "宏忆", "陶", "崧", "鑫辉", "兹披", "数披", "扫描全能王",
]


def _parse_rules(s: str | None) -> list[tuple[str, str]] | None:
    if not s:
        return None
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"规则格式应为 原文=替换文本，收到：{part}")
        a, b = part.split("=", 1)
        out.append((a.strip(), b.strip()))
    return out


def verify(pdf: str | Path, keys: list[str] | None = None, dpi: int = 200) -> dict:
    """
    脱敏后自检：对输出文件重新 OCR，找出仍含敏感词的页。

    必须做这一步 —— 脱敏依赖于「OCR 定位 + 覆盖」，OCR 没认出来的文字，
    坐标也定位不到，自然遮不上。不复查就交付，很可能交出一份看起来处理过、
    实际还留着真名的文件。
    """
    keys = keys or VERIFY_KEYS
    doc = fitz.open(str(pdf))
    dirty: list[dict] = []
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        txt = pytesseract.image_to_string(img, lang="chi_sim", config="--psm 4")
        flat = "".join(txt.split())
        hits = [k for k in keys if k in flat]
        if hits:
            dirty.append({"page": i + 1, "keys": hits})
    n = doc.page_count
    doc.close()
    return {"pages": n, "dirty": dirty, "clean": len(dirty) == 0,
            "clean_pages": n - len(dirty)}


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描件凭证脱敏")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--rules", help="自定义规则，形如 原文=替换,原文2=替换2")
    ap.add_argument("--dpi", type=int, default=300, help="OCR 渲染精度，默认 300")
    ap.add_argument("--psm", type=int, default=4, help="tesseract 版面模式，默认 4")
    ap.add_argument("--no-verify", action="store_true", help="跳过脱敏后自检")
    args = ap.parse_args()

    print(f"输入：{args.src}")
    stat = desensitize(args.src, args.dst, rules=_parse_rules(args.rules),
                       dpi=args.dpi, psm=args.psm)
    print("\n=== 脱敏完成 ===")
    print(f"页数：{stat['pages']}　遮盖：{stat['hits']} 处　输出：{stat['output']}"
          f"（{stat['size_mb']} MB）")
    for k, v in sorted(stat["by_rule"].items(), key=lambda x: -x[1]):
        print(f"  {k}  ×{v}")
    if stat["hits"] == 0:
        print("⚠️ 未命中任何规则。请确认 OCR 是否正常，或检查敏感词的写法。")

    if args.no_verify:
        return 0

    print("\n=== 自检（重新 OCR 检查残留）===")
    v = verify(stat["output"])
    if v["clean"]:
        print(f"✅ {v['pages']}/{v['pages']} 页均未检出敏感词残留。")
        print("   仍需人工抽查：OCR 完全没认出的文字，本自检也查不到。")
    else:
        print(f"⚠️ {len(v['dirty'])}/{v['pages']} 页仍检出残留，需人工处理：")
        for d in v["dirty"]:
            print(f"   p{d['page']}: {'、'.join(d['keys'])}")
        print("   处理办法：把具体写法补进 --rules，或在图片编辑里手动覆盖该处。")
    return 0 if v["clean"] else 2


if __name__ == "__main__":
    sys.exit(main())
