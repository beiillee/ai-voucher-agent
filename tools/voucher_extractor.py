# -*- coding: utf-8 -*-
"""
Voucher Extractor Tool
======================
职责：从 PDF Parser 返回的每页 OCR/文本里提取结构化凭证字段。
设计：基于已确认真实布局的规则提取（凭证头每页重复、合计行各 2 个金额等）。
  - 严格保留 OCR 原文（raw_text）
  - 字段提取失败时标 None，绝不编造
  - 跨页聚合：用众数合并 OCR 噪声导致的 None/错页，保证同一本凭证正确归组
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# ---- 正则模式 ----
# 凭证号：第 1211 号（允许中间空格）
RE_VOUCHER_NO = re.compile(r"第\s*(\d{1,6})\s*号")
RE_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_PAGE_X_OF_N = re.compile(r"(\d{1,3})\s*[/／]\s*(\d{1,3})")
# 金额：支持千分位(逗号/空格分隔) + 小数点后可能有空格，如 41, 778, 905. 63
RE_AMOUNT = re.compile(r"\d{1,3}(?:[,\s]+\d{3})+\.\s*\d{1,2}|\d+\.\s*\d{1,2}")
RE_COMPANY = re.compile(r"核算单位[^一-龥]{0,30}?([一-龥]{2,40}?(?:有限公司|股份公司|集团|公司))")
RE_TOTAL = re.compile(r"合\s*计\s*Total", re.IGNORECASE)

# ---- 补充字段（用户原始规格要求；OCR 噪声下 best-effort，抽不到标 None）----
# 附件数：凭证头 "附件 N 张"
RE_ATTACHMENT = re.compile(r"附\s*件\s*(\d+)\s*张")
# 交易对手：采购暂估凭证每行形如 "应付账款|采购暂估|<供应商公司>"
# 锚定在公司后缀（含 OCR 截断变体），避免把下一列文字粘进名称
RE_COUNTERPARTY = re.compile(
    r"暂\s*估\s*\|?\s*([一-龥A-Za-z0-9（）().\-]{2,40}?"
    r"(?:有限公司|有限公|限公司|股份公司|集团|公司|厂|局|部))"
)
# 仅保留像公司名的串（含公司后缀），过滤 OCR 串列噪声
RE_CP_SUFFIX = re.compile(r"(公司|厂|局|部|集团|股份)")
# 发票 / 合同 关键字段
RE_INVOICE = re.compile(r"发\s*票[号码：: ]*([\w\-]{4,})")
RE_CONTRACT = re.compile(r"合\s*同[号码编号：: ]*([\w\-]{4,})")

# ---- 会计科目识别 ----
# 凭证正文每行形如：  摘要|交易对手  会计科目  金额
# 科目取自「金额之前的最后一个中文词」，再用科目词表校验，避免把公司名当科目。
RE_LINE_WITH_AMOUNT = re.compile(
    r"^(?P<body>.+?)\s+(?P<amount>\d{1,3}(?:[,\s]+\d{3})*\.\s*\d{1,2})\s*$"
)
# 常见科目词表（按长度降序匹配，长的优先，避免「管理费用」被切成「费用」）
ACCOUNT_VOCAB = [
    "主营业务收入", "主营业务成本", "其他业务收入", "其他业务成本",
    "税金及附加", "销售费用", "管理费用", "财务费用", "制造费用", "研发费用",
    "营业外收入", "营业外支出", "所得税费用", "资产减值损失", "信用减值损失",
    "库存现金", "银行存款", "其他货币资金", "应收账款", "应付账款",
    "预收账款", "预付账款", "其他应收款", "其他应付款", "应收票据", "应付票据",
    "原材料", "库存商品", "周转材料", "在产品", "半成品", "委托加工物资",
    "固定资产", "累计折旧", "在建工程", "无形资产", "累计摊销", "长期待摊费用",
    "短期借款", "长期借款", "应付职工薪酬", "应交税费", "实收资本", "资本公积",
    "盈余公积", "本年利润", "利润分配", "生产成本", "劳务成本", "合同负债",
    "合同资产", "使用权资产", "租赁负债", "递延收益", "待摊费用", "预提费用",
    "业务招待费", "差旅费", "办公费", "职工薪酬", "折旧费", "摊销费",
    "运输费", "仓储费", "广告费", "咨询服务费", "技术服务费", "水电费",
    "咨询费", "服务费", "劳务费", "会议费", "培训费", "租赁费", "保险费",
    "修理费", "低值易耗品", "包装物", "销售费用-业务招待费",
]
# 兜底：以这些词结尾的中文串也认作科目（覆盖企业自定义明细科目）
ACCOUNT_SUFFIX = ("费", "成本", "收入", "支出", "损失", "账款", "存款", "现金",
                  "税金", "资产", "负债", "权益", "利润", "准备", "物资", "商品",
                  "材料", "票据", "薪酬", "工程", "摊销", "折旧", "基金")
# 不是科目的常见误命中
ACCOUNT_STOP = {"合计", "小计", "金额", "借方", "贷方", "摘要", "科目", "会计科目",
                "记账凭证", "核算单位", "制单", "审核", "记账", "附件"}

# 摘要中的业务动词，用于判断一行是不是分录行
RE_LINE_NOISE = re.compile(r"合\s*计|Total|记账凭证|核算单位|摘\s*要|会计科目", re.IGNORECASE)

# 交易对手名清洗：去空格，过滤纯数字/过短噪声
def _norm_counterparty(s: str) -> str:
    s = re.sub(r"\s", "", s or "")
    return s.strip()


def _clean_amount(s: str) -> float | None:
    """清洗金额字符串：去千分位空格/逗号，转 float。"""
    if not s:
        return None
    t = re.sub(r"(?<=\d)[\s,](?=\d)", "", s)
    t = t.replace(",", "").replace(" ", "")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _find_account(text: str) -> str | None:
    """
    从一段文本里找会计科目。

    先用词表做最长匹配（词表里的标准科目最可信）；词表没命中时，
    退化为「以科目后缀结尾的连续中文串」，但仍要排除合计/金额这类表头词。
    找不到就返回 None，不猜。
    """
    if not text:
        return None
    t = re.sub(r"\s", "", text)
    if not t or t in ACCOUNT_STOP:
        return None
    # 0) 去掉公司名及其前缀：行内常是「供应商全称 + 科目」，
    #    不剥离的话会把「XX有限公司咨询费」整串当科目。
    m_co = re.search(r"(?:有限公司|股份公司|集团|公司|厂|局|部)", t)
    body = t[m_co.end():] if (m_co and m_co.end() < len(t)) else t
    if not body:
        body = t
    # 1) 词表最长匹配（先在剥离后的正文上找，找不到再退回整串）
    vocab_sorted = sorted(ACCOUNT_VOCAB, key=len, reverse=True)
    for vocab in vocab_sorted:
        if vocab in body:
            return vocab
    for vocab in vocab_sorted:
        if vocab in t:
            return vocab
    # 2) 后缀兜底：取末尾的连续中文（含括号），长度 2-12
    m = re.search(r"([一-龥（）()]{2,12})$", body)
    if m:
        cand = m.group(1)
        if cand not in ACCOUNT_STOP and cand.endswith(ACCOUNT_SUFFIX) and len(cand) >= 3:
            return cand
    return None


def _extract_line_items(text: str) -> list[dict]:
    """
    抽取凭证正文的分录行：{summary, account, amount, raw_line}。

    凭证行的一般形态是「摘要|交易对手  科目  金额」，科目在金额左侧。
    只处理能明确切出行首摘要与行尾金额的行，其余一律跳过，宁可少抽也不错抽。
    """
    items: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or RE_LINE_NOISE.search(line):
            continue
        m = RE_LINE_WITH_AMOUNT.match(line)
        if not m:
            continue
        body = m.group("body").strip()
        amount = _clean_amount(m.group("amount"))
        if amount is None:
            continue
        # 摘要：竖线之前的部分；没有竖线时取行首 2-20 字
        summary = None
        head = body
        if "|" in body or "｜" in body:
            parts = re.split(r"[|｜]", body, maxsplit=1)
            summary = parts[0].strip()
            head = parts[1].strip()
        else:
            mm = re.match(r"^([一-龥]{2,20})", body)
            summary = mm.group(1) if mm else None
        account = _find_account(head)
        if not account:
            continue
        items.append({
            "summary": summary,
            "account": account,
            "amount": amount,
            "raw_line": line,
        })
    return items


def _extract_page_fields(text: str) -> dict[str, Any]:
    """从单页 OCR 文本里提取字段（粗提取，不做跨页合并）。"""
    fields: dict[str, Any] = {
        "voucher_no": None, "date": None, "company": None,
        "page_x_of_n": None, "amounts_all": [],
        "debit_total": None, "credit_total": None, "line_amounts": [],
        "line_items": [], "accounts": [], "summaries": [],
        "attachment": None, "counterparties": [], "invoices": [], "contracts": [],
    }
    m = RE_VOUCHER_NO.search(text)
    if m:
        fields["voucher_no"] = m.group(1)
    m = RE_DATE.search(text)
    if m:
        fields["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = RE_COMPANY.search(text)
    if m:
        fields["company"] = m.group(1).strip()
    m = RE_PAGE_X_OF_N.search(text)
    if m:
        try:
            fields["page_x_of_n"] = (int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    # 所有金额（去重保持顺序）
    seen: set[str] = set(); amts: list[float] = []
    for raw in RE_AMOUNT.findall(text):
        v = _clean_amount(raw)
        if v is None:
            continue
        key = f"{v:.2f}"
        if key in seen:
            continue
        seen.add(key); amts.append(v)
    fields["amounts_all"] = amts

    # 附件数（凭证头，每页重复，取众数在聚合阶段处理）
    m_att = RE_ATTACHMENT.search(text)
    if m_att:
        try:
            fields["attachment"] = int(m_att.group(1))
        except ValueError:
            fields["attachment"] = None
    # 交易对手（采购暂估行里的供应商公司名，best-effort）
    for raw in RE_COUNTERPARTY.findall(text):
        name = _norm_counterparty(raw)
        # 过滤纯数字/过短/无公司后缀的噪声串
        if len(name) >= 4 and RE_CP_SUFFIX.search(name):
            fields["counterparties"].append(name)
    # 发票 / 合同 关键字段
    for raw in RE_INVOICE.findall(text):
        fields["invoices"].append(raw.strip())
    for raw in RE_CONTRACT.findall(text):
        fields["contracts"].append(raw.strip())

    # 合计行：取"合计"之后出现的金额；最大者为借方合计(=凭证总额)，其余之和为本页贷方小计
    m_total = RE_TOTAL.search(text)
    if m_total:
        tail = text[m_total.end(): m_total.end() + 400]
        tail_amts: list[float] = []
        for raw in RE_AMOUNT.findall(tail):
            v = _clean_amount(raw)
            if v is not None:
                tail_amts.append(v)
        if tail_amts:
            debit = max(tail_amts)
            credit = round(sum(tail_amts) - debit, 2)
            fields["debit_total"] = debit
            fields["credit_total"] = credit
            fields["line_amounts"] = [a for a in amts if a != debit]
    else:
        fields["line_amounts"] = amts

    # 分录行（摘要 + 会计科目 + 金额）：匹配与风险分析都要用到科目口径
    fields["line_items"] = _extract_line_items(text)
    fields["accounts"] = list(dict.fromkeys(
        it["account"] for it in fields["line_items"] if it.get("account")))
    fields["summaries"] = list(dict.fromkeys(
        it["summary"] for it in fields["line_items"] if it.get("summary")))

    return fields


def _most_common(xs: list) -> Any:
    if not xs:
        return None
    c = Counter(xs)
    return c.most_common(1)[0][0]


def extract_vouchers(pdf_parse_result: dict) -> list[dict[str, Any]]:
    """
    主入口：聚合跨页凭证。
    稳健分组：先按凭证号分组；OCR 噪声导致凭证号为 None 的页，归入众数凭证号；
    若存在多个不同非 None 凭证号，则分别成组。日期/公司用众数补全。
    """
    pages = pdf_parse_result.get("pages") or []
    per_page: list[dict] = []
    for p in pages:
        f = _extract_page_fields(p.get("text", ""))
        f["page_no"] = p.get("page_no")
        f["ocr_confidence"] = p.get("ocr_confidence")
        per_page.append(f)

    page_count_total = len(per_page)
    # 主凭证号（众数）
    vno_counter = Counter(f["voucher_no"] for f in per_page if f["voucher_no"])
    main_vno = vno_counter.most_common(1)[0][0] if vno_counter else None
    # 全局日期/单位仅作兜底：只有当某张凭证的所有页都抽不到日期(或单位)时才用它。
    # 不能用全文件众数直接填每张凭证 —— 多张凭证的 PDF 会全部串成同一天，
    # 进而使 period 错误、匹配降级为「仅编号」并产生假的日期不一致。
    global_date = _most_common([f["date"] for f in per_page if f["date"]])
    global_company = _most_common([f["company"] for f in per_page if f["company"]])

    # 分组：key = (vno if vno else main_vno)
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for f in per_page:
        key = f["voucher_no"] if f["voucher_no"] else (main_vno or "__unknown__")
        if key not in groups:
            groups[key] = []; order.append(key)
        groups[key].append(f)

    vouchers: list[dict] = []
    for key in order:
        items = groups[key]
        vno = key if key != "__unknown__" else None
        # 日期/单位按「本凭证所辖的页」取众数；全抽不到时退回全局众数
        main_date = _most_common([it["date"] for it in items if it.get("date")]) or global_date
        main_company = _most_common([it["company"] for it in items if it.get("company")]) or global_company
        # 借方合计(凭证总额)：综合两类信号取众数，稳健应对 OCR 噪声：
        #   - 各页「合计」行的借方(=本页总额)：清晰/文字层 PDF 可靠
        #   - 全局 ≥ 百万 金额：扫描件 OCR 下「合计」行漏检时的退路
        #   二者合并后取众数；若均不重复，优先取合计行最大值(更可能为整张凭证总额)。
        page_totals = [it["debit_total"] for it in items if it.get("debit_total") is not None]
        all_amts = [a for it in items for a in (it.get("amounts_all") or [])]
        big = [a for a in all_amts if a >= 1_000_000]
        candidates = page_totals + big
        if candidates:
            top = Counter(candidates).most_common(1)[0]
            if top[1] >= 2:
                debit_total = top[0]
            else:
                debit_total = max(page_totals) if page_totals else (max(big) if big else None)
        else:
            page_maxes = [max(it["amounts_all"]) for it in items if it.get("amounts_all")]
            debit_total = _most_common(page_maxes)
        # 贷方合计：各页"合计"行小计之和
        credits = [it["credit_total"] for it in items if it["credit_total"] is not None]
        credit_total_sum = round(sum(credits), 2) if credits else None
        period = ((main_date or "")[:7]) if vno else None

        # 跨页分录行金额
        line_amounts_union: list[float] = []
        seen = set()
        for it in items:
            for a in it.get("line_amounts") or []:
                k2 = f"{a:.2f}"
                if k2 in seen:
                    continue
                seen.add(k2); line_amounts_union.append(a)

        # 补充字段聚合（best-effort，跨页去重）
        # 附件数：取众数（凭证头每页重复同一数值）
        atts = [it["attachment"] for it in items if it.get("attachment") is not None]
        attachment = _most_common(atts) if atts else None
        # 交易对手：跨页去重（按归一化名），按出现频次排序，截断避免噪声膨胀
        cp_norm: dict[str, int] = {}
        for it in items:
            for cp in (it.get("counterparties") or []):
                cp_norm[cp] = cp_norm.get(cp, 0) + 1
        counterparties = [c for c, _ in sorted(cp_norm.items(), key=lambda kv: -kv[1])][:30]
        invoices = list({iv for it in items for iv in (it.get("invoices") or [])})
        contracts = list({ct for it in items for ct in (it.get("contracts") or [])})

        # 分录行 / 科目 / 摘要（跨页合并，保持出现顺序）
        line_items_all: list[dict] = [li for it in items for li in (it.get("line_items") or [])]
        accounts_all = list(dict.fromkeys(
            a for it in items for a in (it.get("accounts") or [])))
        summaries_all = list(dict.fromkeys(
            s for it in items for s in (it.get("summaries") or [])))
        # 主科目：金额最大的一行所属科目，供风险分析按科目归类
        primary_account = None
        if line_items_all:
            primary = max(line_items_all, key=lambda x: x.get("amount") or 0)
            primary_account = primary.get("account")

        confs = [it["ocr_confidence"] for it in items if it["ocr_confidence"] is not None]
        ocr_conf_avg = round(sum(confs) / len(confs), 1) if confs else None

        # 抽取完整度分母固定为 4（编号/日期/单位/借方合计），加科目不摊薄原有口径
        fields_present = sum(1 for x in [vno, main_date, main_company, debit_total] if x)
        extraction_conf = round(fields_present / 4.0, 2)

        unparsed = []
        if not vno: unparsed.append("voucher_no")
        if not main_date: unparsed.append("date")
        if not main_company: unparsed.append("company")
        if debit_total is None: unparsed.append("debit_total")
        if not accounts_all: unparsed.append("accounts")

        vouchers.append({
            "voucher_no_raw": vno,
            "voucher_no": vno,
            "date": main_date,
            "period": period,
            "company": main_company,
            "source_pages": [it["page_no"] for it in items],
            "page_count": len(items),
            "total_pages": page_count_total,
            "debit_total": debit_total,
            "credit_total_pages": credit_total_sum,
            "debit_sum": debit_total,          # 与序时账统一命名
            "credit_sum": credit_total_sum,
            "line_amounts": line_amounts_union,
            "line_items": line_items_all,
            "accounts": accounts_all,
            "summaries": summaries_all,
            "primary_account": primary_account,
            "line_count_pdf": sum(len(it.get("line_amounts") or []) for it in items),
            "line_count": len(line_items_all),
            "ocr_confidence_avg": ocr_conf_avg,
            "extraction_confidence": extraction_conf,
            "attachment": attachment,
            "counterparties": counterparties,
            "counterparty_count": len(counterparties),
            "invoices": invoices,
            "contracts": contracts,
            "raw_text_combined": "\n".join(
                p.get("text") or "" for p in pages if p.get("page_no") in [it["page_no"] for it in items]),
            "unparsed_fields": unparsed,
        })
    return vouchers


def normalize_pdf_voucher_key(voucher: dict) -> tuple:
    """归一化 PDF 凭证为 (type, period, seq) 用于跨源匹配。"""
    seq = voucher.get("voucher_no")
    period = voucher.get("period")
    return ("记", period, seq)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from .ocr import load_cache
    cache = Path("/workspace/ai_voucher_agent/data/ocr_cache.json")
    if not cache.exists():
        print("请先运行 tools/ocr.py 生成缓存"); sys.exit(1)
    pages = load_cache(cache)
    vs = extract_vouchers({"pages": pages})
    print(f"提取凭证: {len(vs)} 张")
    for v in vs:
        print({k: v[k] for k in
               ("voucher_no", "date", "period", "company", "debit_total",
                "credit_total_pages", "page_count", "ocr_confidence_avg",
                "extraction_confidence", "line_count_pdf", "unparsed_fields")})
