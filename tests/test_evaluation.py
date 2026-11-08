# -*- coding: utf-8 -*-
"""
评估模块自测
============
评估器本身也要被验证，否则「指标 100%」可能只是算错了。

这里用构造的预测结果与标准答案，覆盖：
  - TP / FP / FN / TN 四种情形是否归位
  - 找错对象时同时计 FP 与 FN（不能只算一边）
  - is_info 条目不得进入异常评估
  - 字段准确率按字段分组，不用总分掩盖单项全错
"""
import unittest

from evaluation.metrics import prf, field_accuracy, false_positive_rate
from evaluation.evaluate import evaluate_matching, evaluate_anomalies, evaluate_fields


def pair(vno, ledger_vno, status):
    return {
        "pdf_voucher": {"voucher_no": vno},
        "ledger_voucher": {"voucher_no": ledger_vno} if ledger_vno else None,
        "match_status": status,
    }


class TestMetrics(unittest.TestCase):
    def test_prf_values(self):
        m = prf(3, 1, 2)
        self.assertAlmostEqual(m["precision"], 0.75, 4)
        self.assertAlmostEqual(m["recall"], 0.6, 4)
        self.assertAlmostEqual(m["f1"], 2 * 0.75 * 0.6 / 1.35, 4)

    def test_prf_zero_denominator(self):
        m = prf(0, 0, 0)
        self.assertEqual((m["precision"], m["recall"], m["f1"]), (0.0, 0.0, 0.0))

    def test_fpr(self):
        self.assertAlmostEqual(false_positive_rate(1, 3), 0.25, 4)
        self.assertEqual(false_positive_rate(0, 0), 0.0)

    def test_field_accuracy_grouping(self):
        checks = [
            {"field": "date", "ok": True}, {"field": "date", "ok": False},
            {"field": "account", "ok": True},
        ]
        r = field_accuracy(checks)
        self.assertAlmostEqual(r["accuracy"], 2 / 3, 4)
        self.assertAlmostEqual(r["by_field"]["date"]["accuracy"], 0.5, 4)
        self.assertAlmostEqual(r["by_field"]["account"]["accuracy"], 1.0, 4)


class TestMatchingEvaluation(unittest.TestCase):
    def _labels(self):
        return {"expected_pairs": {
            "A": {"ledger_voucher_no": "L-A", "status": "MATCHED"},
            "B": {"ledger_voucher_no": "L-B", "status": "AMOUNT_MISMATCH"},
            "C": {"ledger_voucher_no": None, "status": "PDF_ONLY"},
            "D": {"ledger_voucher_no": "L-D", "status": "MATCHED"},
            "E": {"ledger_voucher_no": "L-E", "status": "MATCHED"},
        }}

    def test_all_four_outcomes(self):
        res = {"reconciliation": {"pairs": [
            pair("A", "L-A", "MATCHED"),          # TP
            pair("B", "L-B", "AMOUNT_MISMATCH"),  # TP
            pair("C", None, "PDF_ONLY"),          # TN
            pair("D", "L-X", "MATCHED"),          # 找错对象：FP + FN
            # E 缺失：FN
        ]}}
        m = evaluate_matching(res, self._labels())
        self.assertEqual(m["tp"], 2)
        self.assertEqual(m["tn"], 1)
        self.assertEqual(m["fp"], 1)
        self.assertEqual(m["fn"], 2)      # D 找错 + E 漏找
        self.assertAlmostEqual(m["precision"], 2 / 3, 4)
        self.assertAlmostEqual(m["recall"], 0.5, 4)

    def test_status_accuracy_only_counts_correct_object(self):
        res = {"reconciliation": {"pairs": [
            pair("A", "L-A", "DATE_MISMATCH"),    # 对象对、状态错
            pair("B", "L-B", "AMOUNT_MISMATCH"),
            pair("C", None, "PDF_ONLY"),
            pair("D", "L-D", "MATCHED"),
            pair("E", "L-E", "MATCHED"),
        ]}}
        m = evaluate_matching(res, self._labels())
        self.assertEqual(m["tp"], 4)
        self.assertAlmostEqual(m["status_accuracy"], 0.75, 4)


class TestAnomalyEvaluation(unittest.TestCase):
    def test_info_entries_excluded(self):
        res = {"anomalies": {"anomalies": [
            {"voucher_no": "0003", "type": "AMOUNT_MISMATCH"},
            {"voucher_no": None, "type": "LEDGER_ONLY", "is_info": True},
            {"voucher_no": None, "type": "OCR_UNCERTAIN", "is_info": True},
        ]}}
        labels = {"expected_anomalies": [{"voucher_no": "0003", "type": "AMOUNT_MISMATCH"}],
                  "expected_pairs": {"0001": {}, "0003": {}}}
        a = evaluate_anomalies(res, labels)
        self.assertEqual(a["tp"], 1)
        self.assertEqual(a["fp"], 0, "信息性条目不能算误报")
        self.assertEqual(a["precision"], 1.0)

    def test_false_positive_rate_by_voucher(self):
        res = {"anomalies": {"anomalies": [
            {"voucher_no": "0001", "type": "PDF_ONLY"},
        ]}}
        labels = {"expected_anomalies": [{"voucher_no": "0003", "type": "PDF_ONLY"}],
                  "expected_pairs": {"0001": {}, "0003": {}, "0004": {}}}
        a = evaluate_anomalies(res, labels)
        self.assertEqual(a["fp"], 1)
        self.assertEqual(a["fn"], 1)
        # 3 个凭证里 1 个被误报 -> FPR = 1/(1+2)
        self.assertAlmostEqual(a["false_positive_rate"], round(1 / 3, 4), 4)


class TestFieldEvaluation(unittest.TestCase):
    def test_account_must_be_in_list(self):
        res = {"pdf_vouchers": [
            {"voucher_no": "0001", "date": "2025-01-06", "debit_total": 100.0,
             "accounts": ["原材料", "应付账款"]},
        ]}
        labels = {"expected_fields": {
            "0001": {"voucher_no": "0001", "date": "2025-01-06",
                     "debit_total": 100.0, "account": "原材料"}}}
        f = evaluate_fields(res, labels)
        self.assertEqual(f["correct"], 4)

    def test_wrong_amount_detected(self):
        res = {"pdf_vouchers": [
            {"voucher_no": "0001", "date": "2025-01-06", "debit_total": 99.0,
             "accounts": ["原材料"]},
        ]}
        labels = {"expected_fields": {
            "0001": {"voucher_no": "0001", "date": "2025-01-06",
                     "debit_total": 100.0, "account": "原材料"}}}
        f = evaluate_fields(res, labels)
        self.assertEqual(f["correct"], 3)
        self.assertAlmostEqual(f["by_field"]["debit_total"]["accuracy"], 0.0, 4)


if __name__ == "__main__":
    unittest.main()
