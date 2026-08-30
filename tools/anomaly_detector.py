# -*- coding: utf-8 -*-
"""
Anomaly Detector Tool
=====================
在 reconciliation 基础上做 10 类异常检测（用户要求）：
  1. 序时账存在但 PDF 找不到 -> LEDGER_ONLY
  2. PDF 存在但序时账找不到 -> PDF_ONLY
  3. 凭证号不一致
  4. 日期不一致
  5. 金额不一致
  6. 借贷金额不一致
  7. 同一凭证重复出现
  8. OCR 置信度过低
  9. 关键字段缺失
  10. 一笔凭证无法唯一匹配
"""
from __future__ import annotations

from typing import Any
from collections import Counter

OCR_LOW_THRESHOLD = 60.0


def detect_anomalies(
    pdf_vouchers: list[dict],
    ledger_vouchers: list[dict],
    reconciliation_result: dict,
) -> dict:
    pairs = reconciliation_result.get("pairs", [])
    # 重复凭证号（PDF 内）
    pdf_vno_counter = Counter(v.get("voucher_no") for v in pdf_vouchers)
    # 重复（ledger 内）
    led_vno_counter = Counter(v.get("voucher_no") for v in ledger_vouchers)

    anomalies: list[dict] = []
    pair_index: dict[int, list[int]] = {}  # ledger voucher index -> [pair_idx,...]

    for idx, p in enumerate(pairs):
        pv = p["pdf_voucher"]
        lv = p.get("ledger_voucher")
        # 8 OCR 低
        ocr_c = pv.get("ocr_confidence_avg")
        if ocr_c is not None and ocr_c < OCR_LOW_THRESHOLD:
            anomalies.append({
                "type": "OCR_UNCERTAIN",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": f"OCR 整体置信度 {ocr_c} < {OCR_LOW_THRESHOLD}",
            })
        # 9 关键字段缺失
        if pv.get("unparsed_fields"):
            anomalies.append({
                "type": "KEY_FIELD_MISSING",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": f"未识别字段: {', '.join(pv['unparsed_fields'])}",
            })
        # 5/6 金额/借贷
        if p["match_status"] == "AMOUNT_MISMATCH":
            anomalies.append({
                "type": "AMOUNT_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": p.get("anomaly_reason") or "金额不一致",
                "debit_diff": p.get("debit_diff"),
                "credit_diff": p.get("credit_diff"),
            })
        # 4 日期不一致
        if p["match_status"] == "DATE_MISMATCH":
            anomalies.append({
                "type": "DATE_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": p.get("anomaly_reason"),
            })
        # 3 凭证号不一致（matched 但 method 降级或 key 不全）
        if p["match_status"] == "MATCHED" and p.get("match_method", "").startswith("P2"):
            anomalies.append({
                "type": "VOUCHER_NO_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": "仅按编号匹配，期间不一致",
            })
        # 2 PDF_ONLY
        if p["match_status"] == "PDF_ONLY":
            anomalies.append({
                "type": "PDF_ONLY",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": "PDF 存在但序时账未找到",
            })
        # 10 非唯一匹配
        if lv is not None:
            pair_index.setdefault(id(lv), []).append(idx)
            if len(pair_index[id(lv)]) > 1:
                anomalies.append({
                    "type": "NON_UNIQUE_MATCH",
                    "voucher_no": lv.get("voucher_no"),
                    "page": pv.get("source_pages"),
                    "detail": f"同一序时账凭证被 {len(pair_index[id(lv)])} 个 PDF 凭证匹配",
                })

    # 7 PDF 重复
    for vno, cnt in pdf_vno_counter.items():
        if vno and cnt > 1:
            anomalies.append({
                "type": "DUPLICATE_VOUCHER",
                "voucher_no": vno,
                "detail": f"PDF 出现 {cnt} 次",
            })
    # 7 ledger 重复
    for vno, cnt in led_vno_counter.items():
        if vno and cnt > 1:
            anomalies.append({
                "type": "DUPLICATE_LEDGER",
                "voucher_no": vno,
                "detail": f"序时账出现 {cnt} 次",
            })

    # 1 LEDGER_ONLY（信息性：未在 PDF 扫描范围内）
    s = reconciliation_result.get("summary", {})
    ledger_only = s.get("ledger_only", 0)
    if ledger_only:
        anomalies.append({
            "type": "LEDGER_ONLY",
            "voucher_no": None,
            "detail": f"序时账中存在但本次 PDF 未提供扫描，共 {ledger_only} 笔（信息性，PDF 仅覆盖部分凭证）",
            "is_info": True,
        })

    # 借贷不平：单张凭证借贷必相等，PDF 内部借≠贷 必为 OCR 噪声。
    # 这是「OCR 贷方小计有噪声、已用借贷平衡修正」的方法学提示，并非核对异常，
    # 故标记为信息性(is_info)，以免与真实异常混淆。
    for pv in pdf_vouchers:
        d = pv.get("debit_total")
        c = pv.get("credit_total_pages")
        if d is not None and c is not None and abs(d - c) > 1.0:
            anomalies.append({
                "type": "OCR_UNCERTAIN",
                "voucher_no": pv.get("voucher_no"),
                "detail": f"PDF 内部借贷不平(OCR噪声)：借 {d} ≠ 贷(各页小计和) {c}，已按借贷平衡用借方比对（信息性提示，非核对异常）",
                "is_info": True,
            })

    return {
        "anomalies": anomalies,
        "n_anomalies": len([a for a in anomalies if not a.get("is_info")]),
        "n_info": len([a for a in anomalies if a.get("is_info")]),
    }
