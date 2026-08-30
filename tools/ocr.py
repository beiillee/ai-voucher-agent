# -*- coding: utf-8 -*-
"""
OCR Tool
========
职责：把 PDF 扫描页渲染为图片并用 tesseract 进行中文 OCR。
输入：PDF 路径
输出：每页 OCR 文本 + 整体置信度（来自 image_to_data 的 word conf 平均）
可选：保存渲染图片到目录，便于溯源。
"""
from __future__ import annotations

import io
import os
import json
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pytesseract
from PIL import Image


def render_page(doc: fitz.Document, page_no_0: int, dpi: int = 300) -> Image.Image:
    """渲染第 page_no_0 页（0 起）为高清 PIL 图。"""
    page = doc[page_no_0]
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def ocr_image(img: Image.Image, lang: str = "chi_sim", psm: int = 6) -> str:
    return pytesseract.image_to_string(img, lang=lang, config=f"--psm {psm}")


def ocr_image_confidence(img: Image.Image, lang: str = "chi_sim", psm: int = 6) -> float | None:
    """取整页 OCR 词级置信度的平均值（0-100）。"""
    data = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    confs: list[int] = []
    for c in data.get("conf", []):
        try:
            v = int(float(c))
            if v >= 0:
                confs.append(v)
        except (ValueError, TypeError):
            continue
    return round(sum(confs) / len(confs), 1) if confs else None


def ocr_pdf(
    pdf_path: str | Path,
    dpi: int = 300,
    psm: int = 4,
    lang: str = "chi_sim",
    save_images_dir: str | None = None,
    max_pages: int | None = None,
    fuse_psm: bool = True,
) -> list[dict[str, Any]]:
    """
    对整本 PDF 逐页 OCR。
    返回：[{page_no, text, ocr_confidence, image_size}, ...]
    fuse_psm=True 时：每页同时用 psm=4 与 psm=6 两种布局识别，
    合并文本并取更高的词级置信度，最大化数字(如凭证总额)的捕获率。
    """
    pdf_path = str(pdf_path)
    doc = fitz.open(pdf_path)
    n = doc.page_count
    if max_pages:
        n = min(n, max_pages)
    pages: list[dict[str, Any]] = []
    for pno in range(n):
        img = render_page(doc, pno, dpi=dpi)
        if save_images_dir:
            os.makedirs(save_images_dir, exist_ok=True)
            img.save(os.path.join(save_images_dir, f"page_{pno+1:03d}.png"))
        if fuse_psm:
            t4 = ocr_image(img, lang=lang, psm=4)
            t6 = ocr_image(img, lang=lang, psm=6)
            text = _fuse_texts(t4, t6)
            conf = max(
                ocr_image_confidence(img, lang=lang, psm=4) or 0,
                ocr_image_confidence(img, lang=lang, psm=6) or 0,
            ) or None
        else:
            text = ocr_image(img, lang=lang, psm=psm)
            conf = ocr_image_confidence(img, lang=lang, psm=psm)
        pages.append({
            "page_no": pno + 1,
            "text": text,
            "ocr_confidence": conf,
            "image_size": img.size,
        })
    doc.close()
    return pages


def _fuse_texts(t4: str, t6: str) -> str:
    """
    融合两种 psm 的 OCR 文本：按行对齐，逐行取更长/更完整的一行，
    以补偿单一 psm 的局部漏识（如借贷两列金额错位）。
    """
    l4 = t4.splitlines()
    l6 = t6.splitlines()
    out: list[str] = []
    for i in range(max(len(l4), len(l6))):
        a = l4[i] if i < len(l4) else ""
        b = l6[i] if i < len(l6) else ""
        # 取非空白字符更多的一行（更完整）
        out.append(a if len(a.replace(" ", "")) >= len(b.replace(" ", "")) else b)
    return "\n".join(out)


def save_cache(pages: list[dict], cache_path: str | Path) -> None:
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)


def load_cache(cache_path: str | Path) -> list[dict]:
    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    pdf = sys.argv[1] if len(sys.argv) > 1 else \
        "/root/uploads/1788011094935791699-2023-10-1211.pdf"
    pages = ocr_pdf(pdf, save_images_dir="/workspace/ai_voucher_agent/data/pages")
    save_cache(pages, "/workspace/ai_voucher_agent/data/ocr_cache.json")
    print(f"OCR 完成: {len(pages)} 页")
    for p in pages[:3]:
        print(f"  p{p['page_no']}: conf={p['ocr_confidence']} len={len(p['text'])}")
