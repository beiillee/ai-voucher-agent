# -*- coding: utf-8 -*-
"""
统一字段口径（Schema）
======================
整个项目只在这里定义字段名，其余模块一律引用，避免同一含义出现多个别名
（例如「凭证号 / 凭证编号 / 字号」「金额 / 借方金额 / debit」各自为政）。

三张表的字段口径：
  1. 序时账明细行 record
  2. 序时账凭证 voucher（按凭证号聚合）
  3. PDF 凭证 voucher

字段名统一用英文小写下划线，导出报告时再映射为中文表头（REPORT_LABELS）。
"""
from __future__ import annotations

# ---- 1. 序时账明细行 ----
RECORD_FIELDS = [
    "period",        # 会计期间 YYYY-MM
    "doc_date",      # 记账日期 date | None
    "voucher_type",  # 凭证类型 记/收/付/转
    "voucher_no",    # 凭证号（原样）
    "line_no",       # 分录行号（如有）
    "summary",       # 摘要
    "account",       # 会计科目
    "debit",         # 借方金额
    "credit",        # 贷方金额
    "cash_flow",     # 现金流量项目
    "status",        # 凭证状态
    "maker",         # 制单人
    "checker",       # 审核人
    "booker",        # 记账人
    "counterparty",  # 交易对手
    "source_row",    # Excel 物理行号（1 起，含表头），用于回溯原始文件
]

# ---- 2/3. 凭证级公共字段（PDF 与序时账共用命名）----
VOUCHER_FIELDS = [
    "voucher_no",        # 凭证号
    "period",            # 会计期间 YYYY-MM
    "doc_date",          # 日期
    "debit_sum",         # 借方合计
    "credit_sum",        # 贷方合计
    "summaries",         # 摘要列表
    "accounts",          # 科目列表
    "counterparties",    # 交易对手列表
    "source_rows",       # 序时账物理行号
    "source_pages",      # PDF 页码
    "line_count",        # 分录行数
]

# ---- 匹配状态（全项目唯一枚举）----
# MATCHED          凭证号、期间、金额均一致
# DATE_MISMATCH    凭证可对应，但期间/日期不一致
# AMOUNT_MISMATCH  凭证可对应，但金额不一致
# SUSPECTED        存在多个同样合理的候选，无法唯一确定（不做确定性结论）
# PDF_ONLY         PDF 有、序时账无
# OCR_UNCERTAIN    凭证号未识别，无法匹配
MATCH_STATUS = {
    "MATCHED": "已匹配",
    "DATE_MISMATCH": "日期不一致",
    "AMOUNT_MISMATCH": "金额不一致",
    "SUSPECTED": "疑似匹配",
    "PDF_ONLY": "PDF有/账里无",
    "OCR_UNCERTAIN": "凭证号未识别",
}

# ---- 报告中文表头 ----
REPORT_LABELS = {
    "voucher_no": "凭证号",
    "voucher_no_pdf": "PDF凭证号",
    "voucher_no_ledger": "序时账凭证号",
    "period": "会计期间",
    "doc_date": "日期",
    "debit_sum": "借方合计",
    "credit_sum": "贷方合计",
    "debit_diff": "借方差异",
    "credit_diff": "贷方差异",
    "match_status": "匹配状态",
    "match_method": "匹配方式",
    "match_confidence": "匹配置信度",
    "candidates": "候选数",
    "accounts": "会计科目",
    "summaries": "摘要",
    "source_rows": "序时账行号",
    "source_pages": "PDF页码",
    "anomaly_reason": "异常原因",
}
