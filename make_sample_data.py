# -*- coding: utf-8 -*-
"""
生成脱敏样例数据（不依赖任何 confidential 真实文件）
-------------------------------------------------
产出：
  1) data/sample_ledger.xlsx       —— 脱敏序时账（公司甲/乙/丙 等虚构主体）
  2) data/sample_voucher.pdf       —— 含中文的「文字层」样例凭证 PDF（非扫描件，
                                      可直接被 pdfplumber 抽取，无需 Tesseract）
                                      其中包含两张凭证：
                                        第 1211 号（与序时账完全匹配）
                                        第  999 号（序时账中不存在 → PDF_ONLY）

目的：让项目可脱敏演示 / 可开源 / 可被单元测试离线驱动，
      而不必依赖用户的 confidential 真实凭证与序时账。
"""
from __future__ import annotations

import os
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# ----------------------------------------------------------------------------
# 1) 脱敏序时账 Excel
# ----------------------------------------------------------------------------
def build_ledger() -> pd.DataFrame:
    # 标准列（与真实文件同结构）：凭证编号 / 记账期间 / 摘要 / 会计科目 / 借贷金额
    rows = []

    # ---- 凭证 (记)-2023-10-1211（与 PDF 第1211号完全匹配）----
    # 借方合计 = 贷方合计 = 41,778,905.63（拆成几行，验证“按凭证号聚合”）
    v1211_debit = [
        ("原材料-暂估", "单位应付|常州示例材料有限公司", 41_000_000.00),
        ("原材料-暂估", "单位应付|苏州示例精密制造有限公司", 778_905.63),
    ]
    v1211_credit = [
        ("应付账款-采购暂估", "单位应付|常州示例材料有限公司", 20_889_452.81),
        ("应付账款-采购暂估", "单位应付|苏州示例精密制造有限公司", 20_889_452.82),
    ]
    for acc, summ, amt in v1211_debit:
        rows.append(("(记)-2023-10-1211", "2023-10", summ, acc, amt, 0.0))
    for acc, summ, amt in v1211_credit:
        rows.append(("(记)-2023-10-1211", "2023-10", summ, acc, 0.0, amt))

    # ---- 凭证 (记)-2023-10-1200（仅序时账有 → 信息性 LEDGER_ONLY）----
    rows += [
        ("(记)-2023-10-1200", "2023-10", "支付货款", "银行存款", 120_000.00, 0.0),
        ("(记)-2023-10-1200", "2023-10", "单位应付|宁波示例科技有限公司", "应付账款", 0.0, 120_000.00),
    ]
    # ---- 凭证 (记)-2023-11-1300（仅序时账有 → 信息性 LEDGER_ONLY）----
    rows += [
        ("(记)-2023-11-1300", "2023-11", "计提折旧", "制造费用", 55_500.00, 0.0),
        ("(记)-2023-11-1300", "2023-11", "累计折旧", "累计折旧", 0.0, 55_500.00),
    ]

    cols = ["凭证编号", "记账期间", "摘要", "会计科目", "借方金额", "贷方金额"]
    return pd.DataFrame(rows, columns=cols)


# ----------------------------------------------------------------------------
# 2) 含中文的样例凭证 PDF（文字层，可被 pdfplumber 直接抽取）
# ----------------------------------------------------------------------------
def _add_page(doc, lines: list[str], y=60):
    page = doc.new_page(width=720, height=1000)
    for ln in lines:
        page.insert_text((50, y), ln, fontname="china-s", fontfile=FONT, fontsize=13)
        y += 26
    return page


def build_pdf(path: Path) -> None:
    doc = fitz.open()
    # 第 1211 号 —— 第 1 页（表头 + 部分明细 + 交易对手）
    _add_page(doc, [
        "记账凭证",
        "核算单位：示例精工股份有限公司",
        "第 1211 号   (1 / 2)   2023 年 10 月 31 日",
        "摘要                  会计科目              金额",
        "采购暂估|常州示例材料有限公司      原材料-暂估      41,000,000.00",
        "采购暂估|苏州示例精密制造有限公司  原材料-暂估         778,905.63",
        "（本页为第 1211 号凭证第 1 页，下页续）",
    ])
    # 第 1211 号 —— 第 2 页（续表头 + 合计行）
    _add_page(doc, [
        "记账凭证",
        "核算单位：示例精工股份有限公司",
        "第 1211 号   (2 / 2)   2023 年 10 月 31 日",
        "应付账款-采购暂估  单位应付|常州示例材料有限公司   20,889,452.81",
        "应付账款-采购暂估  单位应付|苏州示例精密制造有限公司 20,889,452.82",
        "合计 Total 41,778,905.63",
    ])
    # 第 999 号 —— 序时账中不存在（用于演示 PDF_ONLY）
    _add_page(doc, [
        "记账凭证",
        "核算单位：示例贸易有限责任公司",
        "第 999 号   (1 / 1)   2023 年 10 月 15 日",
        "采购暂估|杭州示例物资有限公司   原材料-暂估   1,234,567.89",
        "合计 Total 1,234,567.89",
    ])
    doc.save(str(path))
    doc.close()


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    ledger_path = DATA / "sample_ledger.xlsx"
    pdf_path = DATA / "sample_voucher.pdf"

    build_ledger().to_excel(ledger_path, index=False)
    build_pdf(pdf_path)

    print(f"✓ 序时账已生成：{ledger_path}  ({ledger_path.stat().st_size} bytes)")
    print(f"✓ 凭证PDF已生成：{pdf_path}  ({pdf_path.stat().st_size} bytes)")
    print("  说明：PDF 为「文字层」中文凭证（非扫描件），可被 pdfplumber 直接抽取；")
    print("        含 第1211号(与序时账匹配) 与 第999号(序时账无，演示 PDF_ONLY)。")


if __name__ == "__main__":
    main()
