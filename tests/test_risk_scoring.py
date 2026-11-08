# -*- coding: utf-8 -*-
"""
风险评分与维度分析测试
======================
阶段 B 新增的三块能力，每块都要能被真实数据验证：

1. 疑似重复交易（摘要近似，不要求逐字相同）
2. 可解释风险评分：公式公开、可复算
3. 科目 / 月份 / 部门维度汇总

评分断言只校验「按公式算出来的数」，不校验某个魔法值，
避免以后调权重时测试变成绊脚石。
"""
import os
import tempfile
import unittest
from datetime import date

import openpyxl

from tools.excel_analyzer import analyze_excel
from tools.ledger_risk_analyzer import (
    analyze_ledger_risk, LEVEL_WEIGHT, SIM_SIMILAR_MIN,
)


def rec(vno, amount, summary, account="管理费用", period="2025-01",
        doc_date=None, department=None):
    return {
        "voucher_no": vno,
        "period": period,
        "doc_date": doc_date,
        "summary": summary,
        "account": account,
        "debit": amount,
        "credit": 0.0,
        "department": department,
        "source_row": 2,
    }


class TestSimilarTransactions(unittest.TestCase):
    def test_near_duplicate_detected(self):
        """金额相同、摘要只差两个字，应报疑似重复。"""
        records = [
            rec("记-0001", 50_000.0, "采购入库A公司"),
            rec("记-0002", 50_000.0, "采购入库A公司补录"),
        ]
        risk = analyze_ledger_risk(records, has_date_dim=False)
        types = {c["type"] for c in risk["clues"]}
        self.assertIn("SIMILAR_TRANSACTION", types)
        clue = next(c for c in risk["clues"] if c["type"] == "SIMILAR_TRANSACTION")
        self.assertIn("相似度", clue["deviation"])
        self.assertNotEqual(clue["level"], "高", "摘要近似不应与精确重复同级")

    def test_dissimilar_same_amount_not_flagged(self):
        """金额相同但摘要毫无关系，不应报疑似重复。"""
        records = [
            rec("记-0001", 50_000.0, "采购原材料"),
            rec("记-0002", 50_000.0, "支付房租"),
        ]
        risk = analyze_ledger_risk(records, has_date_dim=False)
        self.assertNotIn("SIMILAR_TRANSACTION", {c["type"] for c in risk["clues"]})

    def test_same_voucher_not_flagged(self):
        """同一张凭证内的两条分录不构成重复入账。"""
        records = [
            rec("记-0001", 50_000.0, "采购入库A公司"),
            rec("记-0001", 50_000.0, "采购入库A公司补录"),
        ]
        risk = analyze_ledger_risk(records, has_date_dim=False)
        self.assertNotIn("SIMILAR_TRANSACTION", {c["type"] for c in risk["clues"]})

    def test_threshold_respected(self):
        """相似度低于阈值的一律不报。"""
        records = [
            rec("记-0001", 50_000.0, "采购入库"),
            rec("记-0002", 50_000.0, "采购入库差旅报销"),
        ]
        risk = analyze_ledger_risk(records, has_date_dim=False)
        for c in risk["clues"]:
            if c["type"] == "SIMILAR_TRANSACTION":
                sim = float(c["deviation"].split()[-1])
                self.assertGreaterEqual(sim, SIM_SIMILAR_MIN)


class TestRiskScore(unittest.TestCase):
    def test_formula_is_reproducible(self):
        """评分必须能按公布的公式复算出来。"""
        records = [rec(f"记-{i:04d}", 1000.0 + i, "办公费报销") for i in range(20)]
        risk = analyze_ledger_risk(records, has_date_dim=False)
        s = risk["risk_score"]
        raw = sum(LEVEL_WEIGHT[k] * v for k, v in risk["by_level"].items())
        self.assertEqual(s["raw_weight"], raw)
        expect_density = round(raw / len(records) * 100, 2)
        self.assertAlmostEqual(s["density"], expect_density, 2)
        self.assertAlmostEqual(s["score"], min(100.0, round(expect_density * 2, 1)), 1)

    def test_score_capped_at_100(self):
        records = []
        for i in range(60):
            records.append(rec(f"记-{i:04d}", 50_000.0, "采购入库A公司"))
            records.append(rec(f"记-9{i:04d}", 50_000.0, "采购入库A公司"))
        risk = analyze_ledger_risk(records, has_date_dim=False)
        self.assertLessEqual(risk["risk_score"]["score"], 100.0)

    def test_score_carries_disclaimer(self):
        """评分页必须带口径与免责，不能被当成审计结论。"""
        risk = analyze_ledger_risk([rec("记-0001", 100.0, "办公费")], has_date_dim=False)
        s = risk["risk_score"]
        self.assertIn("formula", s)
        self.assertIn("不代表", s["disclaimer"])


class TestDimensions(unittest.TestCase):
    def test_account_dimension_shares_sum_to_100(self):
        records = [
            rec("记-0001", 600.0, "a", account="管理费用"),
            rec("记-0002", 400.0, "b", account="销售费用"),
        ]
        dim = analyze_ledger_risk(records, has_date_dim=False)["dimensions"]
        accs = {a["account"]: a for a in dim["by_account"]}
        self.assertAlmostEqual(accs["管理费用"]["share_pct"], 60.0, 1)
        self.assertAlmostEqual(accs["销售费用"]["share_pct"], 40.0, 1)
        self.assertAlmostEqual(
            sum(a["share_pct"] for a in dim["by_account"]), 100.0, 1)

    def test_period_dimension_mom(self):
        records = [
            rec("记-0001", 100.0, "a", period="2025-01"),
            rec("记-0002", 150.0, "b", period="2025-02"),
        ]
        dim = analyze_ledger_risk(records, has_date_dim=False)["dimensions"]
        periods = {p["period"]: p for p in dim["by_period"]}
        self.assertIsNone(periods["2025-01"]["mom_pct"])
        self.assertAlmostEqual(periods["2025-02"]["mom_pct"], 50.0, 1)

    def test_department_dimension(self):
        records = [
            rec("记-0001", 300.0, "a", department="研发部"),
            rec("记-0002", 700.0, "b", department="销售部"),
        ]
        dim = analyze_ledger_risk(records, has_date_dim=False)["dimensions"]
        self.assertTrue(dim["has_department"])
        self.assertEqual(dim["by_department"][0]["department"], "销售部")

    def test_no_department_column_degrades(self):
        records = [rec("记-0001", 100.0, "a")]
        dim = analyze_ledger_risk(records, has_date_dim=False)["dimensions"]
        self.assertFalse(dim["has_department"])
        self.assertEqual(dim["by_department"], [])


class TestDepartmentColumnDetection(unittest.TestCase):
    """带「部门」列的真实 Excel 要能自动识别。"""

    def test_detect_department_and_source_row(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["凭证编号", "会计科目", "部门", "借方金额", "贷方金额", "摘要"])
        ws.append(["记-0001", "管理费用", "研发部", 100.0, 0.0, "办公费"])
        ws.append(["记-0002", "销售费用", "销售部", 200.0, 0.0, "差旅费"])
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "ledger.xlsx")
        wb.save(path)

        res = analyze_excel(path)
        self.assertIn("department", res["column_mapping"].values())
        self.assertEqual(res["records"][0]["department"], "研发部")
        # 头行是第 1 行，数据从第 2 行起
        self.assertEqual(res["records"][0]["source_row"], 2)


if __name__ == "__main__":
    unittest.main()
