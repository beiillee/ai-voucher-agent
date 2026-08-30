# -*- coding: utf-8 -*-
"""
Reconciliation Tool
===================
职责：将 PDF 凭证与序时账凭证进行匹配与核对。
匹配优先级（按用户要求）：
  P1 凭证编号 + 期间
  P2 凭证编号
  P3 日期 + 金额 + 摘要/科目
  P4 模糊（金额/期间容差）
输出：每张 PDF 凭证的核对结果 + 仅在 ledger 出现的（LEDGER_ONLY）信息。
"""
from __future__ import annotations

from typing import Any
from collections import defaultdict

from .voucher_extractor import normalize_pdf_voucher_key
from .excel_analyzer import normalize_voucher_key


def _build_ledger_index(ledger_vouchers: list[dict]) -> dict:
    """构建序时账多级索引用于快速匹配。"""
    idx: dict = {
        "by_key": {},            # (type, period, seq) -> voucher
        "by_seq_period": {},     # (period, seq) -> [voucher,...]
        "by_seq": defaultdict(list),  # seq -> [voucher,...]
        "all": ledger_vouchers,  # 全部凭证（供 P4 模糊匹配遍历）
    }
    for lv in ledger_vouchers:
        key = normalize_voucher_key(lv.get("voucher_no"))
        idx["by_key"][key] = lv
        period, seq = key[1], key[2]
        if period and seq:
            idx["by_seq_period"].setdefault((period, seq), []).append(lv)
        if seq:
            idx["by_seq"][seq].append(lv)
    return idx


def _match_one(pdf_voucher: dict, idx: dict) -> dict:
    """对单张 PDF 凭证在 ledger 索引中查找。"""
    result = {
        "pdf_voucher": pdf_voucher,
        "ledger_voucher": None,
        "match_status": "PDF_ONLY",
        "match_method": None,
        "match_confidence": 0.0,
        "debit_diff": None,
        "credit_diff": None,
        "anomaly_reason": "未在序时账中找到对应凭证",
    }
    ptype, pperiod, pseq = normalize_pdf_voucher_key(pdf_voucher)
    if not pseq:
        result["match_status"] = "OCR_UNCERTAIN"
        result["anomaly_reason"] = "PDF 凭证号未识别，无法匹配"
        return result

    # P1 完整 key
    lv = idx["by_key"].get((ptype, pperiod, pseq))
    if lv:
        result.update(_compare(pdf_voucher, lv, "P1_完整key", 1.0))
        return result
    # P1b type 容错：其他类型(收/付)但 period+seq 一致
    cands = idx["by_seq_period"].get((pperiod, pseq)) or []
    if cands:
        lv = cands[0]
        result.update(_compare(pdf_voucher, lv, "P1b_期间+编号", 0.9))
        return result
    # P2 仅 seq（跨期）
    cands = idx["by_seq"].get(pseq) or []
    if cands:
        # 选 debit 最接近的
        best = _pick_closest_amount(cands, pdf_voucher.get("debit_total"))
        result.update(_compare(pdf_voucher, best, "P2_仅编号", 0.7))
        if result["match_status"] == "MATCHED":
            result["match_status"] = "DATE_MISMATCH"
            result["anomaly_reason"] = f"期间不一致(PDF={pperiod}, 序时账={best.get('period')})"
        return result
    # P4 模糊（容 OCR 错）：凭证号被 OCR 轻微误识（如 121l ↔ 1211）
    from difflib import SequenceMatcher
    fuzzy: list[tuple[float, dict]] = []
    for lv in idx["all"]:
        lseq = normalize_voucher_key(lv.get("voucher_no"))[2]
        if not lseq or lseq == pseq:
            continue
        ratio = SequenceMatcher(None, pseq, lseq).ratio()
        if ratio >= 0.8:
            fuzzy.append((ratio, lv))
    if fuzzy:
        fuzzy.sort(key=lambda x: -x[0])
        best = fuzzy[0][1]
        result.update(_compare(pdf_voucher, best, "P4_模糊(容OCR错)", 0.6))
        if result["match_status"] == "MATCHED":
            result["match_status"] = "DATE_MISMATCH"
            result["anomaly_reason"] = f"凭证号OCR近似匹配(PDF={pseq}, 序时账={best.get('voucher_no')})"
        return result
    return result


def _pick_closest_amount(cands: list, target: float | None):
    if target is None:
        return cands[0]
    return min(cands, key=lambda x: abs((x.get("debit_sum") or 0) - target))


def _compare(pdf_v: dict, lv: dict, method: str, base_conf: float) -> dict:
    """比对 PDF 凭证与 ledger 凭证的金额/日期等。"""
    pdf_deb = pdf_v.get("debit_total")
    pdf_cred_pages = pdf_v.get("credit_total_pages")
    lv_deb = lv.get("debit_sum")
    lv_cred = lv.get("credit_sum")
    # 借贷平衡修正：会计凭证借贷必相等；OCR 对「各页贷方小计求和」噪声较大，
    # 若 OCR 贷方与借方(凭证总额)偏差过大，改用借方作为凭证总额预期（借贷平衡）。
    if pdf_cred_pages is None or abs((pdf_cred_pages or 0) - (pdf_deb or 0)) > 1.0:
        pdf_cred_expected = pdf_deb
        credit_from_ocr = False
    else:
        pdf_cred_expected = pdf_cred_pages
        credit_from_ocr = True
    diff_deb = round((pdf_deb or 0) - (lv_deb or 0), 2) if (pdf_deb is not None and lv_deb is not None) else None
    diff_cred = round((pdf_cred_expected or 0) - (lv_cred or 0), 2) if (pdf_cred_expected is not None and lv_cred is not None) else None

    status = "MATCHED"
    reasons: list[str] = []
    if diff_deb is None or abs(diff_deb) > 0.01:
        status = "AMOUNT_MISMATCH"
        reasons.append(f"借方差异 {diff_deb}")
    if diff_cred is None or abs(diff_cred) > 0.01:
        status = "AMOUNT_MISMATCH"
        reasons.append(f"贷方差异 {diff_cred}")
    if not credit_from_ocr and pdf_cred_pages is not None:
        reasons.append(f"(贷方采用借贷平衡修正：OCR小计 {pdf_cred_pages} 不可靠)")
    conf = base_conf
    if pdf_v.get("ocr_confidence_avg") is not None:
        conf = round(base_conf * (pdf_v["ocr_confidence_avg"] / 100.0), 3)
    if not reasons:
        reasons.append("金额匹配")
    return {
        "ledger_voucher": lv,
        "match_status": status,
        "match_method": method,
        "match_confidence": conf,
        "debit_diff": diff_deb,
        "credit_diff": diff_cred,
        "anomaly_reason": "; ".join(reasons) if status != "MATCHED" else "",
    }


def reconcile(pdf_vouchers: list[dict], ledger_vouchers: list[dict]) -> dict:
    """
    主入口。
    返回：
      {
        "pairs": [每个 PDF 凭证的匹配结果],
        "ledger_only_count": ledger 中未被 PDF 覆盖的凭证数,
        "summary": {总, matched, mismatched, pdf_only, ocr_uncertain, match_rate}
      }
    """
    idx = _build_ledger_index(ledger_vouchers)
    matched_ledger_ids: set = set()

    pairs: list[dict] = []
    for pv in pdf_vouchers:
        r = _match_one(pv, idx)
        if r["ledger_voucher"] is not None:
            matched_ledger_ids.add(id(r["ledger_voucher"]))
        pairs.append(r)

    total_ledger = len(ledger_vouchers)
    matched_n = sum(1 for p in pairs if p["match_status"] == "MATCHED")
    mismatched_n = sum(1 for p in pairs if p["match_status"] == "AMOUNT_MISMATCH")
    pdf_only_n = sum(1 for p in pairs if p["match_status"] == "PDF_ONLY")
    ocr_uncertain_n = sum(1 for p in pairs if p["match_status"] == "OCR_UNCERTAIN")
    ledger_only_n = total_ledger - len(matched_ledger_ids)

    pdf_n = len(pdf_vouchers)
    match_rate = round(matched_n / pdf_n * 100, 1) if pdf_n else 0.0

    return {
        "pairs": pairs,
        "matched_ledger_count": len(matched_ledger_ids),
        "ledger_only_count": ledger_only_n,
        "summary": {
            "total_ledger": total_ledger,
            "total_pdf": pdf_n,
            "matched": matched_n,
            "amount_mismatch": mismatched_n,
            "pdf_only": pdf_only_n,
            "ocr_uncertain": ocr_uncertain_n,
            "ledger_only": ledger_only_n,
            "match_rate_pct": match_rate,
        },
    }
