# -*- coding: utf-8 -*-
"""异常检测测试：覆盖 10 类异常 + 信息性条目（LEDGER_ONLY / OCR 噪声）。"""
import unittest

from tools.anomaly_detector import detect_anomalies, OCR_LOW_THRESHOLD


def pdf(vno, **kw):
    base = {
        "voucher_no": vno,
        "period": "2023-10",
        "debit_total": 100.0,
        "credit_total_pages": 100.0,
        "ocr_confidence_avg": 90.0,
        "unparsed_fields": [],
        "source_pages": [1],
    }
    base.update(kw)
    return base


def pair(status, method, pv=None, lv=None, **kw):
    p = {
        "pdf_voucher": pv or pdf("1211"),
        "ledger_voucher": lv,
        "match_status": status,
        "match_method": method,
        "match_confidence": 0.9,
        "debit_diff": None,
        "credit_diff": None,
        "anomaly_reason": "",
    }
    p.update(kw)
    return p


class TestAnomalyDetector(unittest.TestCase):
    def _run(self, pdfs, led, recon_pairs, summary=None):
        recon = {"pairs": recon_pairs, "summary": summary or {}}
        return detect_anomalies(pdfs, led, recon)

    def _types(self, res):
        return {a["type"] for a in res["anomalies"]}

    def test_pdf_only(self):
        res = self._run([pdf("1")], [],
                        [pair("PDF_ONLY", None, pv=pdf("1"))])
        self.assertIn("PDF_ONLY", self._types(res))

    def test_amount_mismatch(self):
        res = self._run([pdf("1")], [],
                        [pair("AMOUNT_MISMATCH", "P1_完整key",
                              debit_diff=1.0, credit_diff=0.0)])
        self.assertIn("AMOUNT_MISMATCH", self._types(res))

    def test_date_mismatch(self):
        res = self._run([pdf("1")], [],
                        [pair("DATE_MISMATCH", "P2_仅编号")])
        self.assertIn("DATE_MISMATCH", self._types(res))

    def test_voucher_no_mismatch(self):
        # 仅按编号匹配(P2)但状态仍为 MATCHED -> 视为凭证号不一致
        res = self._run([pdf("1")], [],
                        [pair("MATCHED", "P2_仅编号", lv=pdf("1"))])
        self.assertIn("VOUCHER_NO_MISMATCH", self._types(res))

    def test_ocr_uncertain_low_conf(self):
        res = self._run([pdf("1", ocr_confidence_avg=OCR_LOW_THRESHOLD - 1)],
                        [], [pair("MATCHED", "P1_完整key",
                                  pv=pdf("1", ocr_confidence_avg=OCR_LOW_THRESHOLD - 1))])
        self.assertIn("OCR_UNCERTAIN", self._types(res))

    def test_key_field_missing(self):
        pv = pdf("1", unparsed_fields=["voucher_no", "date"])
        res = self._run([pv], [], [pair("MATCHED", "P1_完整key", pv=pv)])
        self.assertIn("KEY_FIELD_MISSING", self._types(res))

    def test_non_unique_match(self):
        # 两个 PDF 凭证匹配到同一张序时账凭证
        lv = pdf("1")
        res = self._run([pdf("1"), pdf("1")], [lv],
                        [pair("MATCHED", "P1_完整key", lv=lv),
                         pair("MATCHED", "P1_完整key", lv=lv)])
        self.assertIn("NON_UNIQUE_MATCH", self._types(res))

    def test_duplicate_voucher(self):
        res = self._run([pdf("1"), pdf("1")], [],
                        [pair("MATCHED", "P1_完整key"),
                         pair("MATCHED", "P1_完整key")])
        self.assertIn("DUPLICATE_VOUCHER", self._types(res))

    def test_ledger_only_info(self):
        res = self._run([pdf("1")], [pdf("2")],
                        [pair("MATCHED", "P1_完整key")],
                        summary={"ledger_only": 1})
        self.assertIn("LEDGER_ONLY", self._types(res))
        info = [a for a in res["anomalies"] if a["type"] == "LEDGER_ONLY"][0]
        self.assertTrue(info.get("is_info"))

    def test_ocr_credit_noise_is_info(self):
        # 凭证内部借贷不平（OCR 贷方小计噪声），标记为信息性，不计入实质异常
        pv = pdf("1", debit_total=100.0, credit_total_pages=98.0)
        res = self._run([pv], [], [pair("MATCHED", "P1_完整key", pv=pv)])
        self.assertIn("OCR_UNCERTAIN", self._types(res))
        info = [a for a in res["anomalies"] if a["type"] == "OCR_UNCERTAIN"
                and a.get("is_info")][0]
        self.assertTrue(info.get("is_info"))
        self.assertEqual(res["n_anomalies"], 0)  # 不计入实质异常


if __name__ == "__main__":
    unittest.main()
