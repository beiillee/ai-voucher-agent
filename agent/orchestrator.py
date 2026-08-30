# -*- coding: utf-8 -*-
"""
Orchestrator
============
Agent 编排器：按文件类型与任务自主调度工具链。
流程：
  PDF Parser -> (if 扫描件) OCR -> Voucher Extractor
  Excel Analyzer -> vouchers
  Reconciliation -> pairs
  Anomaly Detector -> anomalies
  Report Generator -> xlsx
  （可选）LLM 对异常生成自然语言解释

确定性逻辑（PDF 解析、Excel 读取、字段映射、匹配、异常检测、报告）
全部在 tools/ 中以纯 Python 实现；LLM 仅在 .env 提供 API Key 时介入
对异常做自然语言解释，不参与任何计算或决策。
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from tools.pdf_parser import parse_pdf
from tools.excel_analyzer import analyze_excel
from tools.voucher_extractor import extract_vouchers
from tools.reconciliation import reconcile
from tools.anomaly_detector import detect_anomalies
from tools.report_generator import generate_report

from .prompts import SYSTEM_PROMPT, ANOMALY_EXPLAIN_USER_TEMPLATE

load_dotenv()


def _maybe_llm_explain(pair: dict) -> str | None:
    """若设置了 OPENAI_API_KEY，对单对异常结果生成中文解释。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        pv = pair["pdf_voucher"]; lv = pair.get("ledger_voucher") or {}
        user_msg = ANOMALY_EXPLAIN_USER_TEMPLATE.format(
            pdf_vno=pv.get("voucher_no"),
            pdf_date=pv.get("date"),
            pdf_debit=pv.get("debit_total"),
            pdf_credit=pv.get("credit_total_pages"),
            ocr_conf=pv.get("ocr_confidence_avg"),
            led_vno=lv.get("voucher_no"),
            led_period=lv.get("period"),
            led_debit=lv.get("debit_sum"),
            led_credit=lv.get("credit_sum"),
            status=pair.get("match_status"),
            method=pair.get("match_method"),
            conf=pair.get("match_confidence"),
            reason=pair.get("anomaly_reason") or "(无)",
        )
        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # 网络/限流/包未装等
        return f"(LLM 不可用: {type(e).__name__}: {e})"


def run(
    pdf_path: str | Path,
    ledger_path: str | Path,
    output_dir: str | Path = "output",
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    端到端主入口。
    返回 dict（含所有中间结果），同时写 Excel 报告到 output_dir。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1) PDF
    pdf_res = parse_pdf(pdf_path, cache_dir=output_dir / "cache")
    # 2) Excel
    ledger_res = analyze_excel(ledger_path)
    # 3) 提取
    pdf_vouchers = extract_vouchers(pdf_res)
    ledger_vouchers = ledger_res["vouchers"]
    # 4) 匹配
    recon = reconcile(pdf_vouchers, ledger_vouchers)
    # 5) 异常
    anom = detect_anomalies(pdf_vouchers, ledger_vouchers, recon)
    # 6) 报告
    report_path = generate_report(output_dir / "voucher_reconciliation_result.xlsx", recon, anom)

    # 7) LLM（可选）
    llm_explanations: dict[int, str] = {}
    llm_used = False
    if use_llm and os.getenv("OPENAI_API_KEY"):
        llm_used = True
        for idx, pair in enumerate(recon["pairs"]):
            if pair.get("match_status") != "MATCHED":
                exp = _maybe_llm_explain(pair)
                if exp:
                    llm_explanations[idx] = exp

    return {
        "pdf": pdf_res,
        "ledger": {
            "source": ledger_res["source"],
            "sheet_name": ledger_res["sheet_name"],
            "n_rows": ledger_res["n_rows"],
            "n_cols": ledger_res["n_cols"],
            "raw_columns": ledger_res["raw_columns"],
            "column_mapping": ledger_res["column_mapping"],
            "n_vouchers": len(ledger_vouchers),
            "warnings": ledger_res["warnings"],
        },
        "pdf_vouchers": pdf_vouchers,
        "reconciliation": recon,
        "anomalies": anom,
        "report_path": str(report_path),
        "llm_used": llm_used,
        "llm_explanations": llm_explanations,
    }


if __name__ == "__main__":
    import sys
    # 不带参数运行时，使用仓库自带的脱敏样例（相对路径，任何电脑均可跑）
    pdf = sys.argv[1] if len(sys.argv) > 1 else "data/sample_voucher.pdf"
    led = sys.argv[2] if len(sys.argv) > 2 else "data/sample_ledger.xlsx"
    out = sys.argv[3] if len(sys.argv) > 3 else "output"  # 相对当前目录，便于随处运行
    res = run(pdf, led, output_dir=out)
    print("\n===== 结果摘要 =====")
    s = res["reconciliation"]["summary"]
    print(f"PDF 凭证: {s['total_pdf']} | 序时账凭证: {s['total_ledger']}")
    print(f"匹配成功: {s['matched']} | 金额不一致: {s['amount_mismatch']} | PDF_ONLY: {s['pdf_only']}")
    print(f"LEDGER_ONLY(信息性): {s['ledger_only']} | OCR 不确定: {s['ocr_uncertain']}")
    print(f"匹配率: {s['match_rate_pct']}%")
    print(f"异常: {res['anomalies']['n_anomalies']} | 信息性: {res['anomalies']['n_info']}")
    print(f"报告: {res['report_path']}")
