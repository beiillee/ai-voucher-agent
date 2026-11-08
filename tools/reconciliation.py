# -*- coding: utf-8 -*-
"""
Reconciliation Tool
===================
职责：把 PDF 凭证与序时账凭证对应起来，并对金额、日期做比对。

匹配阶梯（由强到弱，逐级降级，降级原因写进 match_method）：
  P1  凭证类型 + 期间 + 编号
  P1b 期间 + 编号（凭证类型被 OCR 误识）
  P2  仅编号（跨期，命中后标记为日期不一致）
  P3  金额 + 日期（编号未识别时的退路）
  P4  编号模糊匹配（容忍 OCR 把 1211 识成 121l）

可靠性约定（本项目硬性要求）：
  1. 同号凭证不再互相覆盖：索引的每个键都挂列表，账内同号多张全部保留。
  2. 不静默丢数据：每张 PDF 凭证必有一条结果，每张序时账凭证要么被对应上，
     要么出现在 unmatched_ledger 明细里，不给「只剩一个计数」这种无从追溯的输出。
  3. 多候选时不下确定性结论：金额同样吻合的候选超过一个，状态记为 SUSPECTED，
     并把全部候选写进 candidates 供人工判断。
  4. 金额是否一致由精确比较决定，不由模型或概率分数决定。
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from .voucher_extractor import normalize_pdf_voucher_key
from .excel_analyzer import normalize_voucher_key

# 金额一致的判定口径：分位四舍五入后相等，即视为一致
AMOUNT_TOL = 0.01
# 编号模糊匹配的最低相似度
FUZZY_SEQ_RATIO = 0.8


class _Cand:
    """一个候选对应关系。"""

    __slots__ = ("idx", "lv", "method", "base_conf", "score")

    def __init__(self, idx: int, lv: dict, method: str, base_conf: float) -> None:
        self.idx = idx
        self.lv = lv
        self.method = method
        self.base_conf = base_conf
        self.score = 0.0


def build_ledger_index(ledger_vouchers: list[dict]) -> dict:
    """
    构建序时账多级索引。

    与旧实现的差别：by_key 由「一个键一个凭证」改为「一个键一组凭证」。
    财务上同号凭证重号并非罕见（跨期重号、补录重号），旧写法会让后出现的那张
    静默覆盖前一张，重号凭证于是永远检不出来。
    """
    idx: dict[str, Any] = {
        "by_key": defaultdict(list),        # (type, period, seq) -> [(idx, voucher)]
        "by_seq_period": defaultdict(list),  # (period, seq) -> [(idx, voucher)]
        "by_seq": defaultdict(list),         # seq -> [(idx, voucher)]
        "all": [],                           # [(idx, voucher)] 供模糊匹配
    }
    for i, lv in enumerate(ledger_vouchers):
        key = normalize_voucher_key(lv.get("voucher_no"))
        idx["by_key"][key].append((i, lv))
        period, seq = key[1], key[2]
        if period and seq:
            idx["by_seq_period"][(period, seq)].append((i, lv))
        if seq:
            idx["by_seq"][seq].append((i, lv))
        idx["all"].append((i, lv))
    return idx


# ---------------------------------------------------------------- 打分


def _amount_equal(pv: dict, lv: dict) -> bool:
    a, b = pv.get("debit_total"), lv.get("debit_sum")
    if a is None or b is None:
        return False
    return round(float(a) - float(b), 2) == 0.0 or abs(float(a) - float(b)) <= AMOUNT_TOL


def _text_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a), str(b)).ratio()


def _score(pv: dict, lv: dict, base_conf: float) -> float:
    """
    候选排序用的启发式分数。只用于「在多个候选里挑一个更像的」，
    不参与金额是否一致的判断，也不作为对外结论。
    """
    s = base_conf * 100.0
    a, b = pv.get("debit_total"), lv.get("debit_sum")
    if a is not None and b is not None:
        diff = abs(float(a) - float(b))
        if diff <= AMOUNT_TOL:
            s += 40
        else:
            rel = diff / max(abs(float(a)), abs(float(b)), 1.0)
            s += 25 if rel < 0.001 else (10 if rel < 0.01 else 0)
    if pv.get("period") and pv.get("period") == lv.get("period"):
        s += 15
    # 摘要 / 科目文本相似度，取最高的一对
    pdf_text = " ".join((pv.get("summaries") or []) + (pv.get("accounts") or []))
    led_text = " ".join((lv.get("summaries") or []) + (lv.get("accounts") or []))
    s += 15 * _text_sim(pdf_text, led_text)
    # 日期邻近（3 天内）加分
    pd_, ld_ = pv.get("date"), lv.get("doc_date")
    if pd_ and ld_:
        try:
            from datetime import date
            d1 = pd_ if isinstance(pd_, date) else date.fromisoformat(str(pd_)[:10])
            d2 = ld_ if isinstance(ld_, date) else date.fromisoformat(str(ld_)[:10])
            gap = abs((d1 - d2).days)
            s += 5 if gap <= 3 else (2 if gap <= 10 else 0)
        except (ValueError, TypeError):
            pass
    return round(s, 3)


# ---------------------------------------------------------------- 单张匹配


def _collect_candidates(pv: dict, idx: dict) -> list[_Cand]:
    """按阶梯收集候选，同一张序时账凭证只保留最强的那条阶梯。"""
    ptype, pperiod, pseq = normalize_pdf_voucher_key(pv)
    seen: dict[int, _Cand] = {}

    def add(items, method, conf):
        for i, lv in items:
            if i in seen:
                continue
            seen[i] = _Cand(i, lv, method, conf)

    if pperiod:
        add(idx["by_key"].get((ptype, pperiod, pseq), []), "P1_完整key", 1.0)
        add(idx["by_seq_period"].get((pperiod, pseq), []), "P1b_期间+编号", 0.9)
    add(idx["by_seq"].get(pseq, []), "P2_仅编号", 0.7)

    # P4 编号模糊：仅在没有强候选时才启用，避免把正常匹配带偏
    if not seen:
        for i, lv in idx["all"]:
            lseq = normalize_voucher_key(lv.get("voucher_no"))[2]
            if not lseq or lseq == pseq:
                continue
            if SequenceMatcher(None, pseq, lseq).ratio() >= FUZZY_SEQ_RATIO:
                seen[i] = _Cand(i, lv, "P4_模糊(容OCR错)", 0.6)

    cands = list(seen.values())
    for c in cands:
        c.score = _score(pv, c.lv, c.base_conf)
    cands.sort(key=lambda c: -c.score)
    return cands


def _candidate_brief(c: _Cand) -> dict:
    lv = c.lv
    return {
        "ledger_index": c.idx,
        "voucher_no": lv.get("voucher_no"),
        "period": lv.get("period"),
        "debit_sum": lv.get("debit_sum"),
        "credit_sum": lv.get("credit_sum"),
        "method": c.method,
        "score": c.score,
        "amount_equal": None,  # 由调用方填充
        "source_rows": lv.get("source_rows"),
    }


def _compare(pv: dict, lv: dict, method: str, base_conf: float) -> dict:
    """比对金额。金额比较是精确比较，结论只由数字决定。"""
    pdf_deb = pv.get("debit_total")
    pdf_cred_pages = pv.get("credit_total_pages")
    lv_deb = lv.get("debit_sum")
    lv_cred = lv.get("credit_sum")

    # 借贷平衡修正：会计凭证借贷必相等；OCR 对「各页贷方小计求和」噪声较大，
    # 若 OCR 贷方与借方(凭证总额)偏差过大，改用借方作为贷方预期。
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
    if diff_deb is None or abs(diff_deb) > AMOUNT_TOL:
        status = "AMOUNT_MISMATCH"
        reasons.append(f"借方差异 {diff_deb}")
    if diff_cred is None or abs(diff_cred) > AMOUNT_TOL:
        status = "AMOUNT_MISMATCH"
        reasons.append(f"贷方差异 {diff_cred}")
    if not credit_from_ocr and pdf_cred_pages is not None:
        reasons.append(f"(贷方采用借贷平衡修正：OCR小计 {pdf_cred_pages} 不可靠)")

    conf = base_conf
    if pv.get("ocr_confidence_avg") is not None:
        conf = round(base_conf * (pv["ocr_confidence_avg"] / 100.0), 3)
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


def _base_result(pv: dict) -> dict:
    return {
        "pdf_voucher": pv,
        "ledger_voucher": None,
        "ledger_index": None,
        "match_status": "PDF_ONLY",
        "match_method": None,
        "match_confidence": 0.0,
        "debit_diff": None,
        "credit_diff": None,
        "anomaly_reason": "未在序时账中找到对应凭证",
        "candidates": [],
        "candidate_count": 0,
        "is_ambiguous": False,
    }


def match_one(pv: dict, idx: dict) -> dict:
    """对单张 PDF 凭证在序时账索引中查找。"""
    result = _base_result(pv)
    ptype, pperiod, pseq = normalize_pdf_voucher_key(pv)
    if not pseq:
        result["match_status"] = "OCR_UNCERTAIN"
        result["anomaly_reason"] = "PDF 凭证号未识别，无法匹配"
        return result

    cands = _collect_candidates(pv, idx)
    result["candidate_count"] = len(cands)
    if not cands:
        # P3 退路：编号完全对不上时，用「金额一致 + 期间一致」找回
        p3 = [(i, lv) for i, lv in idx["all"]
              if lv.get("period") == pperiod and _amount_equal(pv, lv)]
        if len(p3) == 1:
            i, lv = p3[0]
            res = _base_result(pv)
            res.update(_compare(pv, lv, "P3_金额+期间", 0.55))
            res["ledger_index"] = i
            res["candidate_count"] = 1
            res["candidates"] = [
                {**_candidate_brief(_Cand(i, lv, "P3_金额+期间", 0.55)), "amount_equal": True}
            ]
            return res
        if len(p3) > 1:
            # 多个金额都一致的候选：不做确定性结论
            b = _base_result(pv)
            b["ledger_index"] = p3[0][0]
            b["ledger_voucher"] = p3[0][1]
            b["match_status"] = "SUSPECTED"
            b["match_method"] = "P3_金额+期间(多候选)"
            b["anomaly_reason"] = f"金额与期间同时吻合的序时账凭证有 {len(p3)} 张，无法唯一确定"
            b["is_ambiguous"] = True
            b["candidates"] = [
                {**_candidate_brief(_Cand(i, lv, "P3_金额+期间", 0.55)), "amount_equal": True}
                for i, lv in p3[:10]
            ]
            b["candidate_count"] = len(p3)
            return b
        return result

    best = cands[0]

    # 歧义判定：金额同样补得上的候选不止一张 -> 只报疑似，不替人拍板
    equal_cands = [c for c in cands if _amount_equal(pv, c.lv)]
    if len(equal_cands) > 1:
        b = _base_result(pv)
        b.update(_compare(pv, best.lv, best.method, best.base_conf))
        b["match_status"] = "SUSPECTED"
        b["match_method"] = f"{best.method}(多候选)"
        b["anomaly_reason"] = (
            f"有 {len(equal_cands)} 张序时账凭证与本凭证金额一致且编号规则相符，"
            f"无法唯一确定对应关系，需人工确认"
        )
        b["is_ambiguous"] = True
        b["ledger_index"] = best.idx
        b["candidates"] = [
            {**_candidate_brief(c), "amount_equal": _amount_equal(pv, c.lv)}
            for c in cands[:10]
        ]
        b["candidate_count"] = len(cands)
        return b

    r = _base_result(pv)
    r.update(_compare(pv, best.lv, best.method, best.base_conf))
    r["ledger_index"] = best.idx
    r["candidates"] = [
        {**_candidate_brief(c), "amount_equal": _amount_equal(pv, c.lv)} for c in cands[:10]
    ]
    r["candidate_count"] = len(cands)

    # 降级匹配的后果：期间对不上 / 编号近似，都要如实标注
    if r["match_status"] == "MATCHED":
        if best.method.startswith("P2"):
            r["match_status"] = "DATE_MISMATCH"
            r["anomaly_reason"] = f"期间不一致(PDF={pperiod}, 序时账={best.lv.get('period')})"
        elif best.method.startswith("P4"):
            r["match_status"] = "DATE_MISMATCH"
            r["anomaly_reason"] = (
                f"凭证号OCR近似匹配(PDF={pseq}, 序时账={best.lv.get('voucher_no')})"
            )
        elif pperiod and best.lv.get("period") and pperiod != best.lv.get("period"):
            r["match_status"] = "DATE_MISMATCH"
            r["anomaly_reason"] = f"期间不一致(PDF={pperiod}, 序时账={best.lv.get('period')})"
    return r


# ---------------------------------------------------------------- 主入口


def reconcile(pdf_vouchers: list[dict], ledger_vouchers: list[dict]) -> dict:
    """
    主入口。

    返回：
      pairs            每个 PDF 凭证一条结果（顺序与输入一致，绝不丢条目）
      unmatched_ledger 未被任何 PDF 凭证对应的序时账凭证明细
      duplicates       重号凭证（PDF 侧与账内分开统计）
      summary          统计口径，含 date_mismatch / suspected 等分项
    """
    idx = build_ledger_index(ledger_vouchers)
    matched_idx: set[int] = set()

    pairs: list[dict] = []
    for pv in pdf_vouchers:
        r = match_one(pv, idx)
        if r.get("ledger_index") is not None:
            matched_idx.add(r["ledger_index"])
        pairs.append(r)

    total_ledger = len(ledger_vouchers)
    unmatched_ledger = [
        {
            "ledger_index": i,
            "voucher_no": lv.get("voucher_no"),
            "period": lv.get("period"),
            "doc_date": lv.get("doc_date"),
            "debit_sum": lv.get("debit_sum"),
            "credit_sum": lv.get("credit_sum"),
            "accounts": lv.get("accounts"),
            "summaries": lv.get("summaries"),
            "source_rows": lv.get("source_rows"),
        }
        for i, lv in enumerate(ledger_vouchers) if i not in matched_idx
    ]

    def count(status: str) -> int:
        return sum(1 for p in pairs if p["match_status"] == status)

    matched_n = count("MATCHED")
    date_mismatch_n = count("DATE_MISMATCH")
    amount_mismatch_n = count("AMOUNT_MISMATCH")
    suspected_n = count("SUSPECTED")
    pdf_only_n = count("PDF_ONLY")
    ocr_uncertain_n = count("OCR_UNCERTAIN")
    ledger_only_n = len(unmatched_ledger)

    # 重号统计：账内同号多张（旧实现里这类凭证会被索引覆盖，永远查不出来）
    by_vno: dict[str, list[int]] = defaultdict(list)
    for i, lv in enumerate(ledger_vouchers):
        vno = lv.get("voucher_no")
        if vno:
            by_vno[str(vno)].append(i)
    dup_ledger = [
        {"voucher_no": v, "count": len(ix), "ledger_index": ix,
         "source_rows": [ledger_vouchers[i].get("source_rows") for i in ix]}
        for v, ix in by_vno.items() if len(ix) > 1
    ]
    dup_pdf: list[dict] = []
    pdf_vno: dict[str, list[int]] = defaultdict(list)
    for i, pv in enumerate(pdf_vouchers):
        vno = pv.get("voucher_no")
        if vno:
            pdf_vno[str(vno)].append(i)
    for v, ix in pdf_vno.items():
        if len(ix) > 1:
            dup_pdf.append({
                "voucher_no": v,
                "count": len(ix),
                "source_pages": [pdf_vouchers[i].get("source_pages") for i in ix],
            })

    pdf_n = len(pdf_vouchers)
    decided = matched_n + date_mismatch_n + amount_mismatch_n
    match_rate = round(decided / pdf_n * 100, 1) if pdf_n else 0.0
    strict_rate = round(matched_n / pdf_n * 100, 1) if pdf_n else 0.0

    return {
        "pairs": pairs,
        "matched_ledger_count": len(matched_idx),
        "ledger_only_count": ledger_only_n,
        "unmatched_ledger": unmatched_ledger,
        "duplicates": {"ledger": dup_ledger, "pdf": dup_pdf},
        "summary": {
            "total_ledger": total_ledger,
            "total_pdf": pdf_n,
            "matched": matched_n,
            "date_mismatch": date_mismatch_n,
            "amount_mismatch": amount_mismatch_n,
            "suspected": suspected_n,
            "pdf_only": pdf_only_n,
            "ocr_uncertain": ocr_uncertain_n,
            "ledger_only": ledger_only_n,
            "match_rate_pct": match_rate,
            "strict_match_rate_pct": strict_rate,
        },
    }
