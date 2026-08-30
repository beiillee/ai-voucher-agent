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


def _extract_page_fields(text: str) -> dict[str, Any]:
    """从单页 OCR 文本里提取字段（粗提取，不做跨页合并）。"""
    fields: dict[str, Any] = {
        "voucher_no": None, "date": None, "company": None,
        "page_x_of_n": None, "amounts_all": [],
        "debit_total": None, "credit_total": None, "line_amounts": [],
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
    main_date = _most_common([f["date"] for f in per_page if f["date"]])
    main_company = _most_common([f["company"] for f in per_page if f["company"]])

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

        confs = [it["ocr_confidence"] for it in items if it["ocr_confidence"] is not None]
        ocr_conf_avg = round(sum(confs) / len(confs), 1) if confs else None

        fields_present = sum(1 for x in [vno, main_date, main_company, debit_total] if x)
        extraction_conf = round(fields_present / 4.0, 2)

        unparsed = []
        if not vno: unparsed.append("voucher_no")
        if not main_date: unparsed.append("date")
        if not main_company: unparsed.append("company")
        if debit_total is None: unparsed.append("debit_total")

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
            "line_amounts": line_amounts_union,
            "line_count_pdf": sum(len(it.get("line_amounts") or []) for it in items),
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
