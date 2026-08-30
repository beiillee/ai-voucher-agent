# -*- coding: utf-8 -*-
"""Excel 序时账分析器测试：字段自动映射、按凭证号聚合、交易对手抽取。"""
import os
import unittest
from pathlib import Path

from tools.excel_analyzer import analyze_excel, normalize_voucher_key

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_XLSX = ROOT / "data" / "sample_ledger.xlsx"


class TestExcelAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = analyze_excel(SAMPLE_XLSX)

    def test_basic_shape(self):
        # 8 行原始记录 -> 聚合 3 张凭证
        self.assertEqual(self.res["n_rows"], 8)
        self.assertEqual(len(self.res["vouchers"]), 3)

    def test_column_mapping(self):
        mp = self.res["column_mapping"]
        self.assertEqual(mp.get("凭证编号"), "voucher_no")
        self.assertEqual(mp.get("借方金额"), "debit")
        self.assertEqual(mp.get("贷方金额"), "credit")

    def test_voucher_1211_aggregation(self):
        v = [x for x in self.res["vouchers"]
             if x["voucher_no"] and "1211" in str(x["voucher_no"])][0]
        self.assertEqual(v["line_count"], 4)               # 4 个分录行
        self.assertAlmostEqual(v["debit_sum"], 41_778_905.63, 2)
        self.assertAlmostEqual(v["credit_sum"], 41_778_905.63, 2)  # 借贷平衡

    def test_ledger_counterparty_extraction(self):
        v = [x for x in self.res["vouchers"]
             if x["voucher_no"] and "1211" in str(x["voucher_no"])][0]
        names = " ".join(v["counterparties"])
        self.assertIn("常州示例材料有限公司", names)
        self.assertIn("苏州示例精密制造有限公司", names)

    def test_normalize_key(self):
        # 与 PDF 侧 normalize_pdf_voucher_key 输出 ('记','2023-10','1211') 对应
        self.assertEqual(
            normalize_voucher_key("(记)-2023-10-1211"),
            ("记", "2023-10", "1211"),
        )


if __name__ == "__main__":
    unittest.main()
