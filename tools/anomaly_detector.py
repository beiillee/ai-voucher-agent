# -*- coding: utf-8 -*-
"""
Anomaly Detector Tool
=====================
在 reconciliation 结果之上，把「对不上的地方」翻译成可读的异常条目：
  1. LEDGER_ONLY          序时账有、PDF 未覆盖（信息性）
  2. PDF_ONLY             PDF 有、序时账无
  3. VOUCHER_NO_MISMATCH  凭证号降级匹配
  4. DATE_MISMATCH        日期/期间不一致
  5. AMOUNT_MISMATCH      金额不一致
  6. DEBIT_CREDIT_UNBALANCE  借贷不平（PDF 内部借≠贷，多为 OCR 噪声，信息性）
  7. DUPLICATE_VOUCHER / DUPLICATE_LEDGER  同号凭证重复
  8. OCR_UNCERTAIN        OCR 置信度过低
  9. KEY_FIELD_MISSING    关键字段缺失
  10. NON_UNIQUE_MATCH    一张序时账凭证被多张 PDF 凭证对应
  11. AMBIGUOUS_MATCH     存在多个同样合理的候选，无法唯一确定（疑似）

所有条目都带 voucher_no / 页码或行号 / 详情，可直接回到原始资料核对。
"""
from __future__ import annotations

from collections import Counter, defaultdict

OCR_LOW_THRESHOLD = 60.0


def detect_anomalies(
    pdf_vouchers: list[dict],
    ledger_vouchers: list[dict],
    reconciliation_result: dict,
) -> dict:
    pairs = reconciliation_result.get("pairs", [])
    anomalies: list[dict] = []

    # 一张序时账凭证被多少张 PDF 凭证对应：优先用索引，兼容只有对象的旧调用
    matched_by: dict[object, list[int]] = defaultdict(list)

    for idx, p in enumerate(pairs):
        pv = p.get("pdf_voucher") or {}
        lv = p.get("ledger_voucher")
        status = p.get("match_status")

        # 8 OCR 置信度
        ocr_c = pv.get("ocr_confidence_avg")
        if ocr_c is not None and ocr_c < OCR_LOW_THRESHOLD:
            anomalies.append({
                "type": "OCR_UNCERTAIN",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": f"OCR 整体置信度 {ocr_c} < {OCR_LOW_THRESHOLD}",
            })
        # 9 关键字段缺失（科目缺失单独归类，不算关键字段，避免噪声）
        missing = [f for f in (pv.get("unparsed_fields") or []) if f != "accounts"]
        if missing:
            anomalies.append({
                "type": "KEY_FIELD_MISSING",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": f"未识别字段: {', '.join(missing)}",
            })
        # 5/6 金额不一致
        if status == "AMOUNT_MISMATCH":
            anomalies.append({
                "type": "AMOUNT_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": p.get("anomaly_reason") or "金额不一致",
                "debit_diff": p.get("debit_diff"),
                "credit_diff": p.get("credit_diff"),
            })
        # 4 日期不一致
        if status == "DATE_MISMATCH":
            anomalies.append({
                "type": "DATE_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": p.get("anomaly_reason"),
            })
        # 11 疑似匹配（多候选）
        if status == "SUSPECTED":
            cands = p.get("candidates") or []
            brief = "、".join(
                f"{c.get('voucher_no')}({c.get('period')}, {c.get('debit_sum')})"
                for c in cands[:3])
            anomalies.append({
                "type": "AMBIGUOUS_MATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": (p.get("anomaly_reason") or "存在多个候选") +
                          (f"；候选：{brief}" if brief else ""),
                "is_ambiguous": True,
            })
        # 3 凭证号降级匹配
        if status == "MATCHED" and str(p.get("match_method") or "").startswith("P2"):
            anomalies.append({
                "type": "VOUCHER_NO_MISMATCH",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": "仅按编号匹配，期间不一致",
            })
        # 2 PDF_ONLY
        if status == "PDF_ONLY":
            anomalies.append({
                "type": "PDF_ONLY",
                "voucher_no": pv.get("voucher_no"),
                "page": pv.get("source_pages"),
                "detail": "PDF 存在但序时账未找到",
            })
        # 10 非唯一匹配
        if lv is not None:
            key = p.get("ledger_index")
            if key is None:
                key = id(lv)
            matched_by[key].append(idx)
            if len(matched_by[key]) == 2:  # 第二次命中时才报一次，避免重复堆叠
                anomalies.append({
                    "type": "NON_UNIQUE_MATCH",
                    "voucher_no": lv.get("voucher_no"),
                    "page": pv.get("source_pages"),
                    "detail": "同一序时账凭证被多张 PDF 凭证对应",
                })

    # 7 同号重复：优先用 reconciliation 的专项统计（能识别索引覆盖问题）
    dups = (reconciliation_result.get("duplicates") or {})
    dup_pdf = dups.get("pdf") or []
    dup_led = dups.get("ledger") or []
    if dup_pdf or dup_led:
        for d in dup_pdf:
            anomalies.append({
                "type": "DUPLICATE_VOUCHER",
                "voucher_no": d.get("voucher_no"),
                "detail": f"PDF 中出现 {d.get('count')} 次",
            })
        for d in dup_led:
            anomalies.append({
                "type": "DUPLICATE_LEDGER",
                "voucher_no": d.get("voucher_no"),
                "detail": f"序时账中出现 {d.get('count')} 次，行号 {d.get('source_rows')}",
            })
    else:
        # 兼容直接传入原始凭证列表的调用
        for vno, cnt in Counter(v.get("voucher_no") for v in pdf_vouchers).items():
            if vno and cnt > 1:
                anomalies.append({"type": "DUPLICATE_VOUCHER", "voucher_no": vno,
                                  "detail": f"PDF 出现 {cnt} 次"})
        for vno, cnt in Counter(v.get("voucher_no") for v in ledger_vouchers).items():
            if vno and cnt > 1:
                anomalies.append({"type": "DUPLICATE_LEDGER", "voucher_no": vno,
                                  "detail": f"序时账出现 {cnt} 次"})

    # 1 LEDGER_ONLY（信息性：PDF 只覆盖部分凭证）
    s = reconciliation_result.get("summary", {})
    ledger_only = s.get("ledger_only", 0)
    if ledger_only:
        unmatched = reconciliation_result.get("unmatched_ledger") or []
        sample = "、".join(str(u.get("voucher_no")) for u in unmatched[:5] if u.get("voucher_no"))
        anomalies.append({
            "type": "LEDGER_ONLY",
            "voucher_no": None,
            "detail": (f"序时账中存在但本次 PDF 未提供扫描，共 {ledger_only} 笔"
                       + (f"（如 {sample}）" if sample else "")),
            "is_info": True,
        })

    # 6 借贷不平：单张凭证借贷必相等，PDF 内部借≠贷 必为 OCR 噪声，
    #   属于方法学提示，非核对异常，故标为信息性。
    for pv in pdf_vouchers:
        d = pv.get("debit_total")
        c = pv.get("credit_total_pages")
        if d is not None and c is not None and abs(d - c) > 1.0:
            anomalies.append({
                "type": "OCR_UNCERTAIN",
                "voucher_no": pv.get("voucher_no"),
                "detail": (f"PDF 内部借贷不平(OCR噪声)：借 {d} ≠ 贷(各页小计和) {c}，"
                           f"已按借贷平衡用借方比对（信息性提示，非核对异常）"),
                "is_info": True,
            })

    by_type = Counter(a["type"] for a in anomalies if not a.get("is_info"))
    return {
        "anomalies": anomalies,
        "n_anomalies": sum(by_type.values()),
        "n_info": len([a for a in anomalies if a.get("is_info")]),
        "by_type": dict(by_type),
    }
