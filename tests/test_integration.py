# -*- coding: utf-8 -*-
"""
端到端集成测试
==============
两个数据集，各测各的：

1) 原样例（data/sample_voucher.pdf + sample_ledger.xlsx）
   —— 2 页凭证、8 行序时账。用于验证不依赖 Tesseract 的最短链路，
      以及报告的 7 个 Sheet 结构。

2) 账证核对演示集（data/demo_recon/）
   —— 8 张账内凭证 + 7 张 PDF 凭证（含 1 张两页），覆盖
      完全匹配 / 金额差异 / 日期差异 / 双向缺失 / 重复入账。
      期望值写在该目录的 labels.json 里，本测试逐项比对，防止回归。

注意：原样例只有 8 行记录，金额比较组样本不足（下限 8 条），
      风险线索理应为 0 条；「风险线索非空」这一项由演示集验证。
"""
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import openpyxl

from agent.orchestrator import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = ROOT / "data" / "sample_voucher.pdf"
SAMPLE_XLSX = ROOT / "data" / "sample_ledger.xlsx"
DEMO_PDF = ROOT / "data" / "demo_recon" / "vouchers.pdf"
DEMO_XLSX = ROOT / "data" / "demo_recon" / "ledger.xlsx"
DEMO_LABELS = ROOT / "data" / "demo_recon" / "labels.json"

EXPECTED_SHEETS = ["核对结果", "异常清单", "未匹配序时账", "风险线索", "风险概览",
                   "维度分析", "统计汇总"]

RISK_CLUE_COLUMNS = ["等级", "类型", "线索", "科目", "期间", "凭证号", "金额",
                     "比较组（样本数）", "基线", "偏离度", "检测方法", "判断依据",
                     "建议核查动作", "证据定位"]


class TestIntegration(unittest.TestCase):
    """原样例链路（文字层 PDF，不触发 OCR）。"""

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

    def test_report_file_created_with_seven_sheets(self):
        rp = Path(self.res["report_path"])
        self.assertTrue(rp.exists())
        wb = openpyxl.load_workbook(rp)
        self.assertListEqual(wb.sheetnames, EXPECTED_SHEETS)

    def test_risk_clue_sheet_structure(self):
        """风险线索表：列名与顺序固定，即使无数据行也要有表头。"""
        wb = openpyxl.load_workbook(self.res["report_path"])
        ws = wb["风险线索"]
        self.assertEqual(ws.max_column, len(RISK_CLUE_COLUMNS))
        self.assertListEqual([c.value for c in ws[1]], RISK_CLUE_COLUMNS)
        # 原样例 8 行记录，比较组样本不足，不应硬凑线索
        self.assertEqual(ws.max_row, 1)
        self.assertEqual(self.res["risk"]["n_clues"], 0)

    def test_risk_overview_sheet_not_empty(self):
        """风险概览表：必须给出记录数、日期维度、比较组数等实际统计值。"""
        wb = openpyxl.load_workbook(self.res["report_path"])
        ws = wb["风险概览"]
        self.assertGreater(ws.max_row, 5)
        kv = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
              for r in range(2, ws.max_row + 1)}
        self.assertIn("序时账记录数", kv)
        self.assertEqual(str(kv["序时账记录数"]), "8")
        self.assertIn("是否含日级日期", kv)
        self.assertEqual(kv["是否含日级日期"], "否")   # 原样例无日期列，应降级
        # 每个概览项都要有「数值/说明」，不能留空
        for k, v in kv.items():
            if k:
                self.assertNotIn(v, (None, ""), f"风险概览项「{k}」缺少取值")

    def test_no_llm_called_without_key(self):
        # 未设置 API Key 时 LLM 不应被调用
        self.assertFalse(self.res["llm_used"])


class TestDemoReconIntegration(unittest.TestCase):
    """账证核对演示集：逐项比对 labels.json 的预期结果。"""

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        cls.res = run(DEMO_PDF, DEMO_XLSX, output_dir=cls.out)
        cls.labels = json.loads(DEMO_LABELS.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_summary_matches_labels(self):
        s = self.res["reconciliation"]["summary"]
        for key, expect in self.labels["expected_summary"].items():
            self.assertEqual(s.get(key), expect, f"summary.{key} 与标准答案不一致")

    def test_match_status_counts(self):
        counts = Counter(p["match_status"] for p in self.res["reconciliation"]["pairs"])
        for status, expect in self.labels["expected_status_counts"].items():
            self.assertEqual(counts.get(status, 0), expect, f"匹配状态 {status} 数量不符")

    def test_anomaly_counts(self):
        anoms = self.res["anomalies"]["anomalies"]
        got_info = Counter(a["type"] for a in anoms if a.get("is_info"))
        got_main = Counter(a["type"] for a in anoms if not a.get("is_info"))
        for t, expect in self.labels["expected_anomaly_counts"]["non_info"].items():
            self.assertEqual(got_main.get(t, 0), expect, f"异常 {t} 数量不符")
        for t, expect in self.labels["expected_anomaly_counts"]["info"].items():
            self.assertEqual(got_info.get(t, 0), expect, f"信息性异常 {t} 数量不符")

    def test_matched_vouchers_identified(self):
        matched = {p["pdf_voucher"].get("voucher_no")
                   for p in self.res["reconciliation"]["pairs"]
                   if p["match_status"] == "MATCHED"}
        self.assertSetEqual(matched, set(self.labels["matched_vouchers"]))

    def test_amount_diff_value(self):
        """0003 的金额差异必须正好是 -2000.00，不能只是「有差异」。"""
        pair = next(p for p in self.res["reconciliation"]["pairs"]
                    if p["pdf_voucher"].get("voucher_no") == "0003")
        self.assertEqual(pair["match_status"], "AMOUNT_MISMATCH")
        self.assertAlmostEqual(pair["debit_diff"], -2000.00, 2)

    def test_multi_page_voucher_aggregated(self):
        """0002 是两页凭证，应合并为一张且总额正确。"""
        pair = next(p for p in self.res["reconciliation"]["pairs"]
                    if p["pdf_voucher"].get("voucher_no") == "0002")
        self.assertEqual(pair["match_status"], "MATCHED")
        self.assertListEqual(pair["pdf_voucher"]["source_pages"], [2, 3])
        self.assertAlmostEqual(pair["pdf_voucher"]["debit_total"], 1_240_000.00, 2)

    def test_report_seven_sheets_with_risk_clue(self):
        """演示集里 0007/0707 同额同摘要，应产出至少一条风险线索。"""
        rp = Path(self.res["report_path"])
        wb = openpyxl.load_workbook(rp)
        self.assertListEqual(wb.sheetnames, EXPECTED_SHEETS)

        ws = wb["风险线索"]
        self.assertGreaterEqual(ws.max_row, 2, "风险线索应有数据行")
        headers = [c.value for c in ws[1]]
        # 每条线索的关键列都不能为空
        for r in range(2, ws.max_row + 1):
            row = dict(zip(headers, [ws.cell(row=r, column=c).value
                                     for c in range(1, ws.max_column + 1)]))
            for col in ("等级", "类型", "线索", "基线", "偏离度", "检测方法", "建议核查动作"):
                self.assertNotIn(row.get(col), (None, ""), f"线索缺少「{col}」：{row.get('线索')}")

        ov = wb["风险概览"]
        kv = {ov.cell(row=r, column=1).value: ov.cell(row=r, column=2).value
              for r in range(2, ov.max_row + 1)}
        self.assertEqual(str(kv["是否含日级日期"]), "是")
        self.assertEqual(str(kv["序时账记录数"]), "16")

    def test_duplicate_booking_flagged_as_risk(self):
        """重复入账（0007 / 0707）应出现在风险线索里，而不是只留在核对结果。"""
        types = [c["type"] for c in self.res["risk"]["clues"]]
        self.assertIn("DUPLICATE_TRANSACTION", types)


if __name__ == "__main__":
    unittest.main()
