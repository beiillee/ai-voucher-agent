# -*- coding: utf-8 -*-
"""端到端集成测试：用脱敏样例(文字层 PDF + 序时账)离线跑通完整 Agent 链路。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.orchestrator import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = ROOT / "data" / "sample_voucher.pdf"
SAMPLE_XLSX = ROOT / "data" / "sample_ledger.xlsx"


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.res = run(SAMPLE_PDF, SAMPLE_XLSX, output_dir=self.out)

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_offline_no_tesseract_required(self):
        # 文字层 PDF：has_text_layer=True，不应触发 OCR
        self.assertTrue(self.res["pdf"]["has_text_layer"])
        self.assertFalse(self.res["pdf"]["used_ocr"])

    def test_match_result(self):
        s = self.res["reconciliation"]["summary"]
        self.assertEqual(s["total_pdf"], 2)
        self.assertEqual(s["total_ledger"], 3)
        self.assertEqual(s["matched"], 1)        # 1211 完全匹配
        self.assertEqual(s["pdf_only"], 1)        # 999 序时账无
        # 1211 借/贷差异均为 0
        for p in self.res["reconciliation"]["pairs"]:
            if p["pdf_voucher"].get("voucher_no") == "1211":
                self.assertAlmostEqual(p["debit_diff"], 0.0, 2)
                self.assertAlmostEqual(p["credit_diff"], 0.0, 2)
                self.assertEqual(p["match_method"], "P1_完整key")

    def test_report_file_created_with_three_sheets(self):
        rp = Path(self.res["report_path"])
        self.assertTrue(rp.exists())
        import openpyxl
        wb = openpyxl.load_workbook(rp)
        self.assertListEqual(wb.sheetnames, ["核对结果", "异常清单", "统计汇总"])

    def test_no_llm_called_without_key(self):
        # 未设置 API Key 时 LLM 不应被调用
        self.assertFalse(self.res["llm_used"])


if __name__ == "__main__":
    unittest.main()
