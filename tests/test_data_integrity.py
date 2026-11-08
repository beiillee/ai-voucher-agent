# -*- coding: utf-8 -*-
"""
数据链路可靠性测试
==================
针对 P0 遗留的四个缺陷，每条都写一个能复现的用例：

1. by_key 普通字典覆盖 -> 同号凭证只剩最后一张，重号永远查不出来
2. 重复凭证检测不可达 -> DUPLICATE_* 计数恒为 1
3. 匹配静默丢数据 -> 只给 ledger_only 计数，没有明细
4. 歧义匹配被当成确定结论 -> 多候选时强行挑一个

这些用例断言的是「不丢、不覆盖、不下确定性结论」，不是实现细节。
"""
import unittest

from tools.reconciliation import reconcile
from tools.excel_analyzer import analyze_excel


def pdf(vno, period, debit, ocr=90.0, pages=(1,)):
    return {
        "voucher_no": vno,
        "period": period,
        "debit_total": debit,
        "credit_total_pages": debit,
        "ocr_confidence_avg": ocr,
        "source_pages": list(pages),
        "accounts": ["银行存款"],
        "summaries": ["测试业务"],
    }


def ledger(vno, period, debit, rows=(2,)):
    return {
        "voucher_no": vno,
        "period": period,
        "debit_sum": debit,
        "credit_sum": debit,
        "accounts": ["银行存款"],
        "summaries": ["测试业务"],
        "source_rows": list(rows),
    }


class TestNoSilentOverwrite(unittest.TestCase):
    """同号凭证不得互相覆盖。"""

    def test_duplicate_voucher_both_kept_in_index(self):
        # 两张同号同期间的凭证（补录重号是真实场景）
        res = reconcile([], [ledger("(记)-2023-10-1211", "2023-10", 100.0, rows=(2, 3)),
                             ledger("(记)-2023-10-1211", "2023-10", 200.0, rows=(4, 5))])
        self.assertEqual(res["summary"]["total_ledger"], 2)
        # 两张都应出现在未匹配明细里，而不是被合并成一张
        self.assertEqual(len(res["unmatched_ledger"]), 2)
        self.assertEqual([u["source_rows"] for u in res["unmatched_ledger"]], [[2, 3], [4, 5]])

    def test_duplicate_detection_reachable(self):
        """同号凭证必须能被检出，旧实现里这类永远计数为 1。"""
        res = reconcile([], [ledger("(记)-2023-10-1211", "2023-10", 100.0),
                             ledger("(记)-2023-10-1211", "2023-10", 100.0)])
        dups = res["duplicates"]["ledger"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["voucher_no"], "(记)-2023-10-1211")
        self.assertEqual(dups[0]["count"], 2)

    def test_pdf_duplicate_detected(self):
        res = reconcile([pdf("0007", "2023-10", 100.0, pages=(1,)),
                         pdf("0007", "2023-10", 100.0, pages=(2,))], [])
        self.assertEqual(len(res["duplicates"]["pdf"]), 1)
        self.assertEqual(res["duplicates"]["pdf"][0]["count"], 2)


class TestNoSilentDataLoss(unittest.TestCase):
    """每张凭证都要有去处：要么匹配上，要么出现在明细里。"""

    def test_every_pdf_voucher_has_result(self):
        pdfs = [pdf("1", "2023-10", 10.0), pdf("2", "2023-10", 20.0),
                pdf("3", "2023-10", 30.0)]
        res = reconcile(pdfs, [ledger("(记)-2023-10-1", "2023-10", 10.0)])
        self.assertEqual(len(res["pairs"]), len(pdfs), "PDF 凭证条目不得丢失")
        # 未被匹配的 PDF 凭证必须是 PDF_ONLY，不能凭空消失
        statuses = [p["match_status"] for p in res["pairs"]]
        self.assertEqual(statuses.count("PDF_ONLY"), 2)

    def test_every_ledger_voucher_accounted_for(self):
        leds = [ledger("(记)-2023-10-1", "2023-10", 10.0),
                ledger("(记)-2023-10-2", "2023-10", 20.0),
                ledger("(记)-2023-10-3", "2023-10", 30.0)]
        res = reconcile([pdf("1", "2023-10", 10.0)], leds)
        matched = len(leds) - len(res["unmatched_ledger"])
        self.assertEqual(matched, res["matched_ledger_count"])
        self.assertEqual(matched + len(res["unmatched_ledger"]), len(leds))
        # 明细里要带凭证号与行号，能回到原表
        for u in res["unmatched_ledger"]:
            self.assertIsNotNone(u["voucher_no"])
            self.assertTrue(u["source_rows"])

    def test_unmatched_detail_carries_source_rows(self):
        res = reconcile([], [ledger("(记)-2023-10-9", "2023-10", 5.0, rows=(11, 12))])
        self.assertEqual(res["unmatched_ledger"][0]["source_rows"], [11, 12])


class TestAmbiguousMatch(unittest.TestCase):
    """多候选时只报疑似，不替人拍板。"""

    def test_two_equal_candidates_is_suspected(self):
        # 同号同金额的两张凭证，PDF 无法区分对应哪一张
        res = reconcile([pdf("1211", "2023-10", 100.0)],
                        [ledger("(记)-2023-10-1211", "2023-10", 100.0),
                         ledger("(记)-2023-10-1211", "2023-10", 100.0)])
        p = res["pairs"][0]
        self.assertEqual(p["match_status"], "SUSPECTED")
        self.assertTrue(p["is_ambiguous"])
        self.assertEqual(p["candidate_count"], 2)
        self.assertGreaterEqual(len(p["candidates"]), 2, "候选明细要留给人工看")
        self.assertEqual(res["summary"]["suspected"], 1)

    def test_unique_candidate_still_conclusive(self):
        # 只有一个候选时，仍给确定结论，不能因为加了歧义判断就全都变疑似
        res = reconcile([pdf("1211", "2023-10", 100.0)],
                        [ledger("(记)-2023-10-1211", "2023-10", 100.0)])
        p = res["pairs"][0]
        self.assertEqual(p["match_status"], "MATCHED")
        self.assertFalse(p["is_ambiguous"])

    def test_amount_conflict_not_treated_as_ambiguous(self):
        # 同号两张但金额不同，且与 PDF 金额完全吻合的只有一张 -> 仍是确定结论
        res = reconcile([pdf("1211", "2023-10", 100.0)],
                        [ledger("(记)-2023-10-1211", "2023-10", 100.0),
                         ledger("(记)-2023-10-1211", "2023-10", 999.0)])
        p = res["pairs"][0]
        self.assertEqual(p["match_status"], "MATCHED")
        self.assertAlmostEqual(p["debit_diff"], 0.0, 2)


class TestSummaryCompleteness(unittest.TestCase):
    """统计口径必须自洽，各项相加等于总数。"""

    def test_date_mismatch_counted(self):
        res = reconcile([pdf("1211", None, 100.0)],
                        [ledger("(记)-2023-11-1211", "2023-11", 100.0)])
        self.assertEqual(res["summary"]["date_mismatch"], 1)

    def test_summary_sums_to_total(self):
        res = reconcile([pdf("1", "2023-10", 10.0), pdf("2", "2023-10", 20.0)],
                        [ledger("(记)-2023-10-1", "2023-10", 10.0)])
        s = res["summary"]
        total = (s["matched"] + s["date_mismatch"] + s["amount_mismatch"]
                 + s["suspected"] + s["pdf_only"] + s["ocr_uncertain"])
        self.assertEqual(total, s["total_pdf"])


class TestSourceRowPreserved(unittest.TestCase):
    """Excel 物理行号必须保留，否则报告无法回溯原表。"""

    def test_source_row_is_physical(self):
        res = analyze_excel("data/demo_recon/ledger.xlsx")
        # 第一行数据位于表头下一行，物理行号从 2 起
        self.assertEqual(res["records"][0]["source_row"], 2)
        rows = [r["source_row"] for r in res["records"]]
        self.assertEqual(rows, sorted(rows), "行号必须单调递增")
        # 凭证聚合要带上其占用的全部行号
        v0 = res["vouchers"][0]
        self.assertEqual(len(v0["source_rows"]), v0["line_count"])


if __name__ == "__main__":
    unittest.main()
