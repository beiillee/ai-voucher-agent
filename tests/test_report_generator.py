# -*- coding: utf-8 -*-
"""
报告生成测试
============
覆盖两种入口：
  - 不传风险分析结果：输出 3 个 Sheet（核对结果 / 异常清单 / 统计汇总）
  - 传入风险分析结果：输出 5 个 Sheet，且风险两张表的内容与字段正确
"""
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
    # 未匹配序时账明细：只给计数不给明细的话，读者无法判断是「没扫描」还是「账里少了」
    "unmatched_ledger": [
        {"voucher_no": "(记)-2023-10-1200", "period": "2023-10",
         "doc_date": None, "debit_sum": 50.0, "credit_sum": 50.0,
         "accounts": ["银行存款", "应收账款"],
         "summaries": ["收款"], "source_rows": [2, 3]},
        {"voucher_no": "(记)-2023-10-1300", "period": "2023-10",
         "doc_date": None, "debit_sum": 60.0, "credit_sum": 60.0,
         "accounts": ["管理费用"], "summaries": ["报销"], "source_rows": [4]},
    ],
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

CLUE_OUTLIER = {
    "type": "AMOUNT_OUTLIER", "level": "高", "title": "差旅费单笔显著偏离同类",
    "account": "差旅费", "period": "2025-03", "voucher_no": "(记)-2025-03-9001",
    "amount": 210_000.0, "peer_group": "差旅费 / 记", "peer_n": 68,
    "baseline": "同组中位数 4,180.00，IQR 上界 9,900.00",
    "deviation": "为组内中位数的 50.2 倍",
    "method": "IQR(1.5) + 稳健 Z(MAD 3.5)",
    "reason": "金额远超同科目同类交易分布",
    "next_step": "调阅原始单据与审批记录",
    "evidence": "凭证 (记)-2025-03-9001，第 3 行",
}
CLUE_DUP = {
    "type": "DUPLICATE_TRANSACTION", "level": "低", "title": "跨期同额同摘要交易",
    "account": "办公费", "period": "2025-02、2025-03", "voucher_no": "a、b",
    "amount": 36_800.0, "peer_group": "金额+摘要+对手方 相同的分录", "peer_n": 2,
    "baseline": "金额 36,800.00、摘要「办公用品采购」",
    "deviation": "出现在 2 张凭证、2 个期间",
    "method": "金额+摘要+对手方 精确匹配",
    "reason": "可能是周期性业务，也可能重复入账",
    "next_step": "比对合同与验收单", "evidence": "凭证 a、b",
}
RISK = {
    "clues": [CLUE_OUTLIER, CLUE_DUP],
    "n_clues": 2, "n_clues_before_trim": 2,
    "by_level": {"高": 1, "中": 0, "低": 1},
    "by_type": {"AMOUNT_OUTLIER": 1, "DUPLICATE_TRANSACTION": 1},
    "coverage": {
        "n_records": 1070, "has_date_dim": True, "temporal_mode": "日级（含期末集中与周末入账）",
        "amount_groups_evaluated": 9, "min_peer_n": 8,
        "amount_groups_skipped": ["制造费用（样本 3 条）"],
        "structure_scope": "费用类科目", "duplicate_buckets_checked": 412,
    },
}


class TestReportGeneratorWithoutRisk(unittest.TestCase):
    """不传风险结果时，出 4 个 Sheet（核对结果 / 异常清单 / 未匹配序时账 / 统计汇总）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "out.xlsx")
        generate_report(self.path, RECON, ANOM)

    def test_four_sheets(self):
        wb = openpyxl.load_workbook(self.path)
        self.assertListEqual(
            wb.sheetnames, ["核对结果", "异常清单", "未匹配序时账", "统计汇总"])

    def test_unmatched_sheet_has_detail_rows(self):
        """未匹配序时账必须是明细，不能只有一个计数。"""
        ws = openpyxl.load_workbook(self.path)["未匹配序时账"]
        self.assertEqual(ws.max_row, 3)          # 表头 + 2 条明细
        headers = [c.value for c in ws[1]]
        self.assertIn("原始行号", headers)
        row_no_col = headers.index("原始行号") + 1
        self.assertEqual(ws.cell(row=2, column=row_no_col).value, "2,3")
        vno_col = headers.index("序时账凭证号") + 1
        self.assertEqual(ws.cell(row=2, column=vno_col).value, "(记)-2023-10-1200")

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


class TestReportGeneratorWithRisk(unittest.TestCase):
    """传入风险结果时，应输出 6 个 Sheet，且风险两张表内容完整。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "out.xlsx")
        generate_report(self.path, RECON, ANOM, RISK)

    def test_six_sheets(self):
        wb = openpyxl.load_workbook(self.path)
        self.assertListEqual(
            wb.sheetnames,
            ["核对结果", "异常清单", "未匹配序时账", "风险线索", "风险概览", "统计汇总"])

    def test_risk_clue_headers(self):
        ws = openpyxl.load_workbook(self.path)["风险线索"]
        self.assertListEqual(
            [c.value for c in ws[1]],
            ["等级", "类型", "线索", "科目", "期间", "凭证号", "金额",
             "比较组（样本数）", "基线", "偏离度", "检测方法",
             "判断依据", "建议核查动作", "证据定位"])
        # 表头 + 2 条线索
        self.assertEqual(ws.max_row, 3)

    def test_risk_clue_rows_not_empty(self):
        """每条线索的判定依据列都必须有值，否则等于给了结论没有证据。"""
        ws = openpyxl.load_workbook(self.path)["风险线索"]
        headers = [c.value for c in ws[1]]
        required = ["等级", "类型", "线索", "基线", "偏离度", "检测方法", "建议核查动作", "证据定位"]
        for r in range(2, ws.max_row + 1):
            row = dict(zip(headers, [ws.cell(row=r, column=c).value
                                     for c in range(1, ws.max_column + 1)]))
            for col in required:
                self.assertNotIn(row[col], (None, ""), f"第 {r} 行缺少「{col}」")

    def test_risk_clue_type_mapped_to_chinese(self):
        """类型列应显示中文名，而不是内部枚举。"""
        ws = openpyxl.load_workbook(self.path)["风险线索"]
        types = [ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)]
        self.assertListEqual(types, ["金额离群", "重复交易"])

    def test_risk_clue_peer_group_carries_sample_size(self):
        """比较组要带上样本数，读者才能判断这个基准可不可信。"""
        ws = openpyxl.load_workbook(self.path)["风险线索"]
        self.assertEqual(ws.cell(row=2, column=8).value, "差旅费 / 记（68 条）")

    def test_risk_overview_values(self):
        ws = openpyxl.load_workbook(self.path)["风险概览"]
        kv = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
              for r in range(2, ws.max_row + 1)}

        def by_label(label):
            """按标签取值：类型分布行带全角缩进，需 strip 后精确比对，
            避免「重复交易」误匹配到「重复交易比对组数」。"""
            for k, v in kv.items():
                if k and str(k).strip() == label:
                    return v
            raise AssertionError(f"风险概览中找不到「{label}」")

        self.assertEqual(str(kv["序时账记录数"]), "1070")
        self.assertEqual(kv["是否含日级日期"], "是")
        self.assertEqual(kv["金额比较组数量"], 9)
        self.assertEqual(kv["风险线索合计"], 2)
        self.assertEqual(kv["高等级"], 1)
        self.assertEqual(kv["低等级"], 1)
        self.assertEqual(by_label("金额离群"), 1)
        self.assertEqual(by_label("重复交易"), 1)
        # 降级/跳过的科目要写明，不能悄悄跳过
        self.assertIn("制造费用", str(kv["样本不足未评估的科目"]))

    def test_risk_overview_method_notes_present(self):
        """方法论提示必须留在报告里：统计异常 ≠ 违规。"""
        ws = openpyxl.load_workbook(self.path)["风险概览"]
        notes = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        self.assertIn("方法说明 1", notes)
        vals = " ".join(str(ws.cell(row=r, column=2).value or "")
                        for r in range(2, ws.max_row + 1))
        self.assertIn("统计异常不等于财务违规", vals)


if __name__ == "__main__":
    unittest.main()
