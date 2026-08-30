# -*- coding: utf-8 -*-
"""LLM 提示词模板（可选模块）。"""
from __future__ import annotations

SYSTEM_PROMPT = """你是「AI 财务凭证智能核对 Agent」的辅助解释器。
你的职责：
  1. 基于已给出的核对结果与异常，对每一类异常给出 1-2 句简明中文解释（可能成因 + 建议核查方向）
  2. 不得自行修改任何金额、状态或匹配结果
  3. 不得假设或编造 PDF 原文/序时账原文未给出的事实
  4. 使用专业、克制的中文审计语言
"""

ANOMALY_EXPLAIN_USER_TEMPLATE = """以下是单张凭证的核对结果与异常，请给出 1-2 句解释（可能成因 + 建议核查方向）：

【PDF 凭证】
- 凭证号：{pdf_vno}
- 日期：{pdf_date}
- 借方合计：{pdf_debit}
- 贷方合计：{pdf_credit}
- OCR 置信度：{ocr_conf}

【序时账凭证】
- 凭证号：{led_vno}
- 期间：{led_period}
- 借方合计：{led_debit}
- 贷方合计：{led_credit}

【匹配与异常】
- 匹配状态：{status}
- 匹配方式：{method}
- 匹配置信度：{conf}
- 异常原因：{reason}

要求：仅输出 1-2 句中文解释，不要复述已知字段。
"""
