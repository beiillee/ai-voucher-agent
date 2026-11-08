# -*- coding: utf-8 -*-
"""
生成账证核对专用演示数据集（ledger + PDF + 标准答案）
=====================================================
产出（均在 data/demo_recon/ 下，不覆盖任何原有样例）：
  ledger.xlsx    —— 8 张凭证的小型序时账，每张凭证借贷各一行
  vouchers.pdf   —— 与账配套的凭证 PDF，共 7 张（含 1 张 2 页凭证）
  labels.json    —— 预期核对结果（标准答案），供端到端断言与后续评估脚本使用

为什么单独做一套：
  data/sample_ledger.xlsx + sample_voucher.pdf 是原作者的 2 页极简样例，
  只覆盖「完全匹配」与「PDF_ONLY」两种情况；
  data/demo_ledger.xlsx 是风险分析用的 535 张凭证大盘，里面没有配套凭证 PDF。
  两者都无法覆盖「金额差异 / 日期差异 / 重复编号 / 双向缺失」，故单建一套。

覆盖的场景（见 SCENARIOS）：
  MATCHED           0001 单页、0002 两页、0009 单页
  AMOUNT_MISMATCH   0003 PDF 比账少 2,000.00
  DATE_MISMATCH     0004 PDF 日期跨到次月，仅编号能对上
  DUPLICATE_LEDGER  0007 账内同号两条（重复录入）
  LEDGER_ONLY       0005 账有 PDF 无（信息性）
  PDF_ONLY          0012 PDF 有账无

用法：
    python make_demo_recon.py
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "demo_recon"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

COMPANY = "示例精工股份有限公司"
COMPANY2 = "示例贸易有限责任公司"

# ---------------------------------------------------------------------------
# 场景定义：账侧凭证（金额单位：元）
#   rows = [(摘要, 借方科目, 贷方科目, 金额)]
# ---------------------------------------------------------------------------
LEDGER_VOUCHERS = [
    # (凭证号, 日期, [(摘要, 借方科目, 贷方科目, 金额)])
    ("(记)-2025-01-0001", "2025-01-06",
     [("采购入库|无锡示例包装有限公司", "原材料", "应付账款", 528_551.91)]),
    ("(记)-2025-01-0002", "2025-01-15",
     [("销售确认|杭州示例物流有限公司", "应收账款", "主营业务收入", 1_240_000.00)]),
    ("(记)-2025-02-0003", "2025-02-10",
     [("咨询服务费|嘉兴示例服务有限公司", "咨询费", "应付账款", 322_300.77)]),
    ("(记)-2025-02-0004", "2025-02-20",
     [("运输费结算|常州示例材料有限公司", "运输费", "应付账款", 95_000.00)]),
    ("(记)-2025-03-0005", "2025-03-08",
     [("计提折旧", "制造费用", "累计折旧", 186_000.00)]),
    ("(记)-2025-03-0007", "2025-03-12",
     [("业务招待|苏州示例精密制造有限公司", "业务招待费", "银行存款", 23_800.00)]),
    # 0707 与 0007 金额/摘要/日期完全相同，仅编号不同 —— 模拟同一笔业务重复入账。
    # 账证核对侧表现为「账有、PDF 未覆盖」，风险分析侧会被识别为重复交易。
    ("(记)-2025-03-0707", "2025-03-12",
     [("业务招待|苏州示例精密制造有限公司", "业务招待费", "银行存款", 23_800.00)]),
    ("(记)-2025-04-0009", "2025-04-08",
     [("差旅费报销|宁波示例科技有限公司", "差旅费", "银行存款", 8_640.00)]),
]

MAKERS = ["张A", "李B", "王C", "赵D"]
CHECKERS = ["陈E", "周F"]


def build_ledger() -> pd.DataFrame:
    rows = []
    for i, (vno, d, items) in enumerate(LEDGER_VOUCHERS):
        for summary, acc_d, acc_c, amt in items:
            rows.append({
                "凭证编号": vno, "记账日期": d, "记账期间": d[:7],
                "摘要": summary, "会计科目": acc_d, "借方金额": round(amt, 2),
                "贷方金额": 0.0, "制单人": MAKERS[i % len(MAKERS)],
                "审核人": CHECKERS[i % len(CHECKERS)],
            })
            rows.append({
                "凭证编号": vno, "记账日期": d, "记账期间": d[:7],
                "摘要": summary, "会计科目": acc_c, "借方金额": 0.0,
                "贷方金额": round(amt, 2), "制单人": MAKERS[i % len(MAKERS)],
                "审核人": CHECKERS[i % len(CHECKERS)],
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# PDF 侧凭证
#   每项：(凭证号, 日期文本, 单位, [页1行...], [页2行...] or None)
#   金额与账一致的用账里的数；0003 故意写少 2,000；0004 日期跨月；0012 账中无
# ---------------------------------------------------------------------------
def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def _total(amt: float) -> str:
    """真实凭证的合计行同时有借贷两栏，此处照实写，避免被当成「借贷不平」的 OCR 噪声。"""
    return f"合计 Total 借 {_fmt(amt)} 贷 {_fmt(amt)}"


PDF_VOUCHERS = [
    # ---- 正常匹配（单页）----
    dict(vno="0001", date="2025 年 1 月 6 日", company=COMPANY, pages=[[
        "采购入库|无锡示例包装有限公司       原材料        528,551.91",
        _total(528551.91),
    ]]),
    # ---- 正常匹配（两页，验证跨页合并）----
    dict(vno="0002", date="2025 年 1 月 15 日", company=COMPANY, pages=[
        ["销售确认|杭州示例物流有限公司      应收账款      1,240,000.00"],
        ["主营业务收入  销售确认|杭州示例物流有限公司",
         _total(1240000.00)],
    ]),
    # ---- 金额差异：账 322,300.77，PDF 写 320,300.77 ----
    dict(vno="0003", date="2025 年 2 月 10 日", company=COMPANY, pages=[[
        "咨询服务费|嘉兴示例服务有限公司     咨询费        320,300.77",
        _total(320300.77),
    ]]),
    # ---- 日期差异：账在 2025-02，PDF 写 2025-03-03 ----
    dict(vno="0004", date="2025 年 3 月 3 日", company=COMPANY, pages=[[
        "运输费结算|常州示例材料有限公司     运输费         95,000.00",
        _total(95000.00),
    ]]),
    # ---- 账内同号 0007：PDF 侧只有一张，对应账里 03-12 那条 ----
    dict(vno="0007", date="2025 年 3 月 12 日", company=COMPANY, pages=[[
        "业务招待|苏州示例精密制造有限公司   业务招待费      23,800.00",
        _total(23800.00),
    ]]),
    # ---- 正常匹配 ----
    dict(vno="0009", date="2025 年 4 月 8 日", company=COMPANY, pages=[[
        "差旅费报销|宁波示例科技有限公司     差旅费          8,640.00",
        _total(8640.00),
    ]]),
    # ---- PDF 有、账中无 ----
    dict(vno="0012", date="2025 年 4 月 22 日", company=COMPANY2, pages=[[
        "采购入库|杭州示例物流有限公司       原材料        777,777.77",
        _total(777777.77),
    ]]),
]


def build_pdf(path: Path) -> int:
    doc = fitz.open()
    n_pages = 0
    for v in PDF_VOUCHERS:
        total_pages = len(v["pages"])
        for i, lines in enumerate(v["pages"], 1):
            page = doc.new_page(width=720, height=1000)
            y = 60
            for ln in [
                "记账凭证",
                f"核算单位：{v['company']}",
                f"第 {v['vno']} 号   ({i} / {total_pages})   {v['date']}",
                "摘要                          会计科目            金额",
            ] + lines:
                page.insert_text((50, y), ln, fontname="china-s", fontfile=FONT, fontsize=13)
                y += 26
            n_pages += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return n_pages


# ---------------------------------------------------------------------------
# 标准答案
# ---------------------------------------------------------------------------
LABELS = {
    "description": "账证核对演示数据集：8 张账内凭证 + 7 张 PDF 凭证（含 1 张两页）",
    "n_ledger_vouchers": 8,
    "n_pdf_vouchers": 7,
    "expected_summary": {
        "total_pdf": 7,
        "total_ledger": 8,
        "matched": 4,
        "amount_mismatch": 1,
        "pdf_only": 1,
        "ledger_only": 2,
    },
    # 注意：reconciliation.summary 目前未统计 DATE_MISMATCH（见 known_gaps），
    # 故按匹配状态与异常清单两侧分别核对。
    "expected_status_counts": {
        "MATCHED": 4,
        "AMOUNT_MISMATCH": 1,
        "DATE_MISMATCH": 1,
        "PDF_ONLY": 1,
    },
    "expected_anomaly_counts": {
        "non_info": {"AMOUNT_MISMATCH": 1, "DATE_MISMATCH": 1, "PDF_ONLY": 1},
        "info": {"LEDGER_ONLY": 1},
    },
    "matched_vouchers": ["0001", "0002", "0007", "0009"],
    "scenarios": [
        {"vno": "0001", "case": "MATCHED", "note": "单页，金额/日期/编号全一致"},
        {"vno": "0002", "case": "MATCHED", "note": "两页凭证，验证跨页聚合后总额一致"},
        {"vno": "0003", "case": "AMOUNT_MISMATCH",
         "note": "账 322,300.77 / PDF 320,300.77，差 2,000.00"},
        {"vno": "0004", "case": "DATE_MISMATCH",
         "note": "账 2025-02-20 / PDF 2025-03-03，仅编号可对上"},
        {"vno": "0005", "case": "LEDGER_ONLY", "note": "账有 PDF 无，信息性线索"},
        {"vno": "0007", "case": "MATCHED", "note": "单页，完全一致"},
        {"vno": "0707", "case": "LEDGER_ONLY",
         "note": "与 0007 金额/摘要/日期相同、编号不同，模拟重复入账；PDF 未覆盖，故为 LEDGER_ONLY"},
        {"vno": "0009", "case": "MATCHED", "note": "单页，完全一致"},
        {"vno": "0012", "case": "PDF_ONLY", "note": "PDF 有、账中无"},
    ],
    # 已知缺口：本数据集不覆盖、且当前实现无法检出的场景。
    # 如实记录，供后续阶段修复时回归，不在此处伪造检出结果。
    "known_gaps": [
        {
            "case": "同号重复凭证（DUPLICATE_LEDGER / DUPLICATE_VOUCHER）",
            "status": "当前实现不可达",
            "reason": "excel_analyzer._aggregate_vouchers 与 voucher_extractor.extract_vouchers "
                      "都按凭证号分组，同号记录在聚合阶段已被合并为一张凭证，"
                      "因此 anomaly_detector 里基于 Counter 的重复检测永远得到计数 1。",
            "suggested_fix": "P3 阶段在 records 层（聚合前）扫描重复凭证号，或在聚合时保留重复组标记。",
        },
        {
            "case": "reconciliation.summary 缺 DATE_MISMATCH 计数",
            "status": "字段缺失",
            "reason": "summary 只统计 matched / amount_mismatch / pdf_only / ocr_uncertain，"
                      "日期不一致只能从 pairs 或 anomalies 反查。",
            "suggested_fix": "P2 阶段在 summary 中补 date_mismatch 字段。",
        },
    ],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ledger_path = OUT / "ledger.xlsx"
    pdf_path = OUT / "vouchers.pdf"
    labels_path = OUT / "labels.json"

    df = build_ledger()
    df.to_excel(ledger_path, index=False)
    n_pages = build_pdf(pdf_path)
    labels_path.write_text(json.dumps(LABELS, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ 序时账：{ledger_path}  ({len(df)} 分行 / {df['凭证编号'].nunique()} 张凭证)")
    print(f"✓ 凭证PDF：{pdf_path}  ({len(PDF_VOUCHERS)} 张凭证 / {n_pages} 页)")
    print(f"✓ 标准答案：{labels_path}")
    for s in LABELS["scenarios"]:
        print(f"    {s['vno']:>4}  {s['case']:<16} {s['note']}")


if __name__ == "__main__":
    main()
