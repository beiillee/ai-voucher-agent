# -*- coding: utf-8 -*-
"""匹配逻辑测试：优先级阶梯 P1 → P1b → P2 → P4，以及金额不一致 / PDF_ONLY。"""
import unittest

from tools.reconciliation import reconcile


def ledger(vno, debit, credit):
    return {"voucher_no": vno, "debit_sum": debit, "credit_sum": credit}


def pdf(vno, period, debit, credit=None, ocr=90.0):
    return {
        "voucher_no": vno,
        "period": period,
        "debit_total": debit,
        "credit_total_pages": credit if credit is not None else debit,
        "ocr_confidence_avg": ocr,
    }


class TestReconciliation(unittest.TestCase):
    def test_p1_full_key(self):
        pairs = reconcile([pdf("1211", "2023-10", 41_778_905.63)],
                          [ledger("(记)-2023-10-1211", 41_778_905.63, 41_778_905.63)])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "MATCHED")
        self.assertEqual(p["match_method"], "P1_完整key")
        self.assertAlmostEqual(p["debit_diff"], 0.0, 2)
        self.assertAlmostEqual(p["match_confidence"], 0.9, 3)  # 1.0 * 0.9

    def test_p1b_period_plus_seq(self):
        # OCR 类型误识为「收」，但 期间+编号 一致
        pairs = reconcile([pdf("1211", "2023-10", 100.0)],
                          [ledger("(收)-2023-10-1211", 100.0, 100.0)])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "MATCHED")
        self.assertEqual(p["match_method"], "P1b_期间+编号")

    def test_p2_cross_period_date_mismatch(self):
        # 仅有编号、无期间 -> 跨期匹配，标记为日期不一致
        pairs = reconcile([pdf("1211", None, 200.0)],
                          [ledger("(记)-2023-11-1211", 200.0, 200.0)])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "DATE_MISMATCH")
        self.assertEqual(p["match_method"], "P2_仅编号")

    def test_p4_fuzzy_ocr_noise(self):
        # 凭证号被 OCR 轻微误识 55555l <-> 555555（编辑距离近似）
        pairs = reconcile([pdf("55555l", "2023-10", 500.0)],
                          [ledger("(记)-2023-10-555555", 500.0, 500.0)])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "DATE_MISMATCH")
        self.assertEqual(p["match_method"], "P4_模糊(容OCR错)")

    def test_amount_mismatch(self):
        pairs = reconcile([pdf("7777", "2023-10", 999.0)],
                          [ledger("(记)-2023-10-7777", 300.0, 300.0)])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "AMOUNT_MISMATCH")
        self.assertAlmostEqual(p["debit_diff"], 699.0, 2)

    def test_pdf_only(self):
        pairs = reconcile([pdf("9999", "2023-10", 123.0)], [])["pairs"]
        p = pairs[0]
        self.assertEqual(p["match_status"], "PDF_ONLY")

    def test_ledger_only_count(self):
        # 序时账多张凭证未被 PDF 覆盖 -> ledger_only 计数
        res = reconcile([pdf("1211", "2023-10", 100.0)],
                        [ledger("(记)-2023-10-1211", 100.0, 100.0),
                         ledger("(记)-2023-10-1200", 50.0, 50.0)])
        self.assertEqual(res["ledger_only_count"], 1)
        self.assertEqual(res["summary"]["ledger_only"], 1)


if __name__ == "__main__":
    unittest.main()
