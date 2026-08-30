# -*- coding: utf-8 -*-
"""报告生成测试：3 个 Sheet、统计汇总数值正确。"""
import os
import tempfile
import unittest

import openpyxl

from tools.report_generator import generate_report

SUMMARY = {
    "total_pdf": 2, "total_ledger": 3, "matched": 1,
    "amount_mismatch": 0, "pdf_only": 1, "ocr_uncertain": 0,
    "ledger_only": 2, "match_rate_pct": 50.0,
}
RECON = {
    "pairs": [
        {"pdf_voucher": {"voucher_no": "1211", "date": "2023-10-31",
                          "company": "示例精工", "debit_total": 41_778_905.63,
                          "credit_total_pages": 41_778_905.63,
                          "ocr_confidence_avg": 92.0, "attachment": None,
                          "counterparty_count": 2,
                          "counterparties": ["常州示例公司", "苏州示例公司"],
                          "invoices": [], "contracts": []},
         "ledger_voucher": {"voucher_no": "(记)-2023-10-1211", "period": "2023-10",
                            "debit_sum": 41_778_905.63, "credit_sum": 41_778_905.63,
                            "counterparties": ["常州示例公司"]},
         "match_status": "MATCHED", "match_method": "P1_完整key",
         "match_confidence": 0.92, "debit_diff": 0.0, "credit_diff": 0.0,
         "anomaly_reason": ""},
        {"pdf_voucher": {"voucher_no": "999", "date": "2023-10-15",
                          "company": "示例贸易", "debit_total": 1_234_567.89,
                          "credit_total_pages": 1_234_567.89,
                          "ocr_confidence_avg": 88.0, "attachment": None,
                          "counterparty_count": 1,
                          "counterparties": ["杭州示例公司"], "invoices": [],
                          "contracts": []},
         "ledger_voucher": None, "match_status": "PDF_ONLY",
         "match_method": None, "match_confidence": 0.0,
         "debit_diff": None, "credit_diff": None,
         "anomaly_reason": "未在序时账中找到对应凭证"},
    ],
    "summary": SUMMARY,
}
ANOM = {
    "anomalies": [
        {"type": "PDF_ONLY", "voucher_no": "999", "page": [3],
         "detail": "PDF 存在但序时账未找到", "is_info": False},
        {"type": "LEDGER_ONLY", "voucher_no": None,
         "detail": "序时账中存在但本次 PDF 未提供扫描，共 2 笔", "is_info": True},
    ],
    "n_anomalies": 1, "n_info": 1,
}


class TestReportGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "out.xlsx")
        generate_report(self.path, RECON, ANOM)

    def test_three_sheets(self):
        wb = openpyxl.load_workbook(self.path)
        self.assertListEqual(wb.sheetnames, ["核对结果", "异常清单", "统计汇总"])

    def test_results_content(self):
        ws = openpyxl.load_workbook(self.path)["核对结果"]
        # 表头 + 2 行数据
        self.assertEqual(ws.max_row, 3)
        # 第 2 行 1211 应为 MATCHED
        status_col = [c.value for c in ws[2]].index("MATCHED") + 1
        self.assertEqual(ws.cell(row=2, column=status_col).value, "MATCHED")

    def test_summary_values(self):
        ws = openpyxl.load_workbook(self.path)["统计汇总"]
        kv = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
              for r in range(2, ws.max_row + 1)}
        self.assertEqual(kv["成功匹配"], 1)
        self.assertEqual(kv["PDF_ONLY（PDF有/序时账无）"], 1)
        self.assertEqual(kv["匹配率"], "50.0%")
        self.assertEqual(kv["异常总数（非信息性）"], 1)


if __name__ == "__main__":
    unittest.main()
