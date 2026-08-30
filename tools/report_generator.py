# -*- coding: utf-8 -*-
"""
Report Generator Tool
=====================
输出 voucher_reconciliation_result.xlsx，含 3 个 Sheet：
  Sheet 1 核对结果：每个 PDF 凭证的核对明细
  Sheet 2 异常清单：所有异常
  Sheet 3 统计汇总：总数 / 匹配率 / 异常分布
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


HEADER_FILL = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF")
ANOMALY_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
MATCHED_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
INFO_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")


def _style_header(ws, n_cols: int) -> None:
    for c in range(1, n_cols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"


def _autosize(ws) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            v = cell.value
            if v is None:
                continue
            # 计算字符宽度（中文按 2 算）
            s = str(v)
            w = sum(2 if ord(ch) > 127 else 1 for ch in s)
            max_len = max(max_len, w)
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 50)


def _write_sheet_results(wb, pairs: list[dict]) -> None:
    ws = wb.active
    ws.title = "核对结果"
    headers = [
        "PDF凭证号", "PDF日期", "PDF核算单位", "PDF借方合计", "PDF贷方合计(各页之和)",
        "序时账凭证号", "序时账期间", "序时账借方合计", "序时账贷方合计",
        "借方差异", "贷方差异", "匹配状态", "匹配方式", "匹配置信度",
        "OCR置信度", "异常原因",
        "PDF附件数", "PDF交易对手数", "PDF交易对手(样例)",
        "序时账交易对手(样例)", "PDF发票/合同数",
    ]
    ws.append(headers)
    _style_header(ws, len(headers))

    for p in pairs:
        pv = p["pdf_voucher"]; lv = p.get("ledger_voucher") or {}
        cp_pdf = pv.get("counterparties") or []
        cp_led = lv.get("counterparties") or []
        row = [
            pv.get("voucher_no"),
            pv.get("date"),
            pv.get("company"),
            pv.get("debit_total"),
            pv.get("credit_total_pages"),
            lv.get("voucher_no"),
            lv.get("period"),
            lv.get("debit_sum"),
            lv.get("credit_sum"),
            p.get("debit_diff"),
            p.get("credit_diff"),
            p.get("match_status"),
            p.get("match_method"),
            p.get("match_confidence"),
            pv.get("ocr_confidence_avg"),
            p.get("anomaly_reason"),
            pv.get("attachment"),
            pv.get("counterparty_count"),
            "、".join(cp_pdf[:5]) + ("…" if len(cp_pdf) > 5 else ""),
            "、".join(cp_led[:5]) + ("…" if len(cp_led) > 5 else ""),
            len((pv.get("invoices") or []) + (pv.get("contracts") or [])),
        ]
        ws.append(row)
        r = ws.max_row
        status = p.get("match_status")
        if status == "MATCHED":
            fill = MATCHED_FILL
        elif p.get("anomaly_reason") and not p.get("match_status") == "MATCHED":
            fill = ANOMALY_FILL
        else:
            fill = None
        if fill:
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).fill = fill
    _autosize(ws)


def _write_sheet_anomalies(wb, anomalies: list[dict]) -> None:
    ws = wb.create_sheet("异常清单")
    headers = ["异常类型", "凭证号", "页码", "详情", "借方差异", "贷方差异", "信息性"]
    ws.append(headers)
    _style_header(ws, len(headers))
    for a in anomalies:
        ws.append([
            a.get("type"),
            a.get("voucher_no"),
            str(a.get("page") or ""),
            a.get("detail"),
            a.get("debit_diff"),
            a.get("credit_diff"),
            "是" if a.get("is_info") else "",
        ])
        r = ws.max_row
        fill = INFO_FILL if a.get("is_info") else ANOMALY_FILL
        for c in range(1, len(headers) + 1):
            ws.cell(row=r, column=c).fill = fill
    _autosize(ws)


def _write_sheet_summary(wb, summary: dict, anomaly_summary: dict) -> None:
    ws = wb.create_sheet("统计汇总")
    ws.append(["指标", "数值", "说明"])
    _style_header(ws, 3)

    rows = [
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""),
        ("PDF 凭证数", summary.get("total_pdf", 0), "PDF 扫描件覆盖的凭证数"),
        ("序时账凭证数", summary.get("total_ledger", 0), "Excel 序时账凭证总数"),
        ("成功匹配", summary.get("matched", 0), "借/贷金额完全一致"),
        ("金额不一致", summary.get("amount_mismatch", 0), ""),
        ("PDF_ONLY（PDF有/序时账无）", summary.get("pdf_only", 0), ""),
        ("OCR 不确定", summary.get("ocr_uncertain", 0), ""),
        ("LEDGER_ONLY（序时账有/PDF无）", summary.get("ledger_only", 0), "信息性：PDF 未覆盖范围"),
        ("匹配率", f"{summary.get('match_rate_pct', 0)}%", "matched / total_pdf"),
        ("异常总数（非信息性）", anomaly_summary.get("n_anomalies", 0), ""),
        ("信息性条目", anomaly_summary.get("n_info", 0), ""),
    ]
    for r in rows:
        ws.append(list(r))
    _autosize(ws)


def generate_report(
    out_path: str | Path,
    reconciliation_result: dict,
    anomaly_result: dict,
) -> str:
    out_path = str(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    wb = openpyxl.Workbook()
    _write_sheet_results(wb, reconciliation_result.get("pairs", []))
    _write_sheet_anomalies(wb, anomaly_result.get("anomalies", []))
    _write_sheet_summary(wb, reconciliation_result.get("summary", {}), anomaly_result)
    wb.save(out_path)
    return out_path
