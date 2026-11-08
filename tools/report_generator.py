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

# 风险线索按等级着色：高=红、中=橙、低=灰
LEVEL_FILL = {
    "高": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
    "中": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
    "低": PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid"),
}
LEVEL_CN = {"AMOUNT_OUTLIER": "金额离群", "PERIOD_END_CLUSTER": "期末集中入账",
            "WEEKEND_POSTING": "周末入账", "MONTH_SWING": "月度剧烈波动",
            "DUPLICATE_TRANSACTION": "重复交易", "STRUCTURE_SHIFT": "费用结构变化"}


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


def _write_sheet_summary(wb, summary: dict, anomaly_summary: dict,
                         risk_result: dict | None = None) -> None:
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
    if risk_result is not None:
        lv = risk_result.get("by_level", {})
        rows += [
            ("", "", ""),
            ("—— 序时账风险分析 ——", "", "对账簿本身的分析，与上方账证核对相互独立"),
            ("风险线索合计", risk_result.get("n_clues", 0), "需进一步核查的线索，非违规结论"),
            ("　高等级", lv.get("高", 0), "优先核查"),
            ("　中等级", lv.get("中", 0), ""),
            ("　低等级", lv.get("低", 0), ""),
        ]
    for r in rows:
        ws.append(list(r))
    _autosize(ws)


def _write_sheet_risk_clues(wb, clues: list[dict]) -> None:
    """风险线索：每条都带比较基准、检测方法、建议核查动作与证据定位。"""
    ws = wb.create_sheet("风险线索")
    headers = ["等级", "类型", "线索", "科目", "期间", "凭证号", "金额",
               "比较组（样本数）", "基线", "偏离度", "检测方法",
               "判断依据", "建议核查动作", "证据定位"]
    ws.append(headers)
    _style_header(ws, len(headers))
    for c in clues:
        peer = c.get("peer_group") or ""
        if c.get("peer_n"):
            peer = f"{peer}（{c['peer_n']} 条）"
        ws.append([
            c.get("level"),
            LEVEL_CN.get(c.get("type"), c.get("type")),
            c.get("title"),
            c.get("account"),
            c.get("period"),
            c.get("voucher_no"),
            c.get("amount"),
            peer,
            c.get("baseline"),
            c.get("deviation"),
            c.get("method"),
            c.get("reason"),
            c.get("next_step"),
            c.get("evidence"),
        ])
        r = ws.max_row
        fill = LEVEL_FILL.get(c.get("level"))
        if fill:
            for col in range(1, len(headers) + 1):
                ws.cell(row=r, column=col).fill = fill
    _autosize(ws)
    ws.freeze_panes = "C2"


def _write_sheet_risk_overview(wb, risk: dict) -> None:
    """风险概览：线索分布 + 各类分析是否实际执行（含降级说明）+ 方法论提示。"""
    ws = wb.create_sheet("风险概览")
    ws.append(["项目", "数值 / 说明", "备注"])
    _style_header(ws, 3)

    cov = risk.get("coverage", {})
    by_level = risk.get("by_level", {})
    by_type = risk.get("by_type", {})

    rows: list[tuple] = [
        ("序时账记录数", cov.get("n_records", 0), "参与分析的明细行数"),
        ("是否含日级日期", "是" if cov.get("has_date_dim") else "否",
         "无日期时，期末集中与周末入账分析自动跳过"),
        ("时间维度分析", cov.get("temporal_mode", ""), "取决于原表是否提供日期列"),
        ("金额比较组数量", cov.get("amount_groups_evaluated", 0),
         f"每个比较组样本量下限 {cov.get('min_peer_n', 0)} 条"),
        ("样本不足未评估的科目", "、".join(cov.get("amount_groups_skipped", [])) or "无",
         "组内样本过少时不强行比较，避免用不足的样本下结论"),
        # 分析器写入的键是 structure_mode（含降级原因），旧版这里读 structure_scope
        # 导致该行恒为空；两个键都兼容，确保「未执行/降级」的原因能显示出来。
        ("结构分析口径", cov.get("structure_scope") or cov.get("structure_mode") or "",
         "费用类科目优先"),
        ("重复交易比对组数", cov.get("duplicate_buckets_checked", 0),
         "金额+摘要+对手方 完全相同的分录组"),
        ("", "", ""),
        ("风险线索合计", risk.get("n_clues", 0),
         f"截断前共 {risk.get('n_clues_before_trim', risk.get('n_clues', 0))} 条，单类型保留偏离最强的若干条"),
        ("高等级", by_level.get("高", 0), "优先核查"),
        ("中等级", by_level.get("中", 0), "安排核查"),
        ("低等级", by_level.get("低", 0), "观察即可"),
        ("", "", ""),
    ]
    for t, cn in LEVEL_CN.items():
        rows.append((f"　{cn}", by_type.get(t, 0), ""))
    rows += [
        ("", "", ""),
        ("方法说明 1", "统计异常不等于财务违规",
         "本表输出的是需要进一步核查的线索，不是结论；是否构成问题须由财务/审计人员判断"),
        ("方法说明 2", "金额比较必须落在比较组内",
         "不同科目、不同业务性质的金额不混在一起比较，避免「金额大=异常」的误判"),
        ("方法说明 3", "比较组样本不足时不出结论",
         "组内样本低于下限时跳过并在此列明，不用不足的样本支撑判断"),
        ("方法说明 4", "每条线索均可追溯",
         "均记录凭证号、期间、科目与检测方法，可直接回到原始账簿核对"),
    ]
    for r in rows:
        ws.append(list(r))
    _autosize(ws)


def generate_report(
    out_path: str | Path,
    reconciliation_result: dict,
    anomaly_result: dict,
    risk_result: dict | None = None,
) -> str:
    out_path = str(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    wb = openpyxl.Workbook()
    _write_sheet_results(wb, reconciliation_result.get("pairs", []))
    _write_sheet_anomalies(wb, anomaly_result.get("anomalies", []))
    if risk_result is not None:
        _write_sheet_risk_clues(wb, risk_result.get("clues", []))
        _write_sheet_risk_overview(wb, risk_result)
    _write_sheet_summary(wb, reconciliation_result.get("summary", {}), anomaly_result,
                         risk_result)
    wb.save(out_path)
    return out_path
