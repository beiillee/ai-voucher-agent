# -*- coding: utf-8 -*-
"""
Excel 序时账分析器 (Excel Analyzer Tool)
========================================
职责：
  1. 读取真实 Excel（不修改原文件）
  2. 自动检测表头行、自动识别字段（关键词 + 样本值启发式）
  3. 将列名映射为标准字段，兼容不同命名习惯
  4. 数值清洗（去千分位、转 float）
  5. 输出：原始 records + 按凭证号聚合后的 vouchers 视图

设计原则（遵循用户要求）：
  - 字段名与预期不同 → 不报错退出，通过字段映射逻辑适配
  - 保留原文件，绝不修改
  - 确定性逻辑全部用 Python，不交给 LLM
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

# ---- 标准字段（项目内部统一 schema）----
# doc_date 为可选字段：真实序时账常见「记账日期/凭证日期」，也可能只有月度期间。
# 有时间维度才能做期末集中入账、周末入账等分析；没有则降级为月度分析。
STANDARD_FIELDS = [
    "period", "doc_date", "voucher_type", "voucher_no", "line_no", "summary",
    "account", "debit", "credit", "cash_flow", "status",
    "maker", "checker", "booker", "counterparty", "department",
]

# ---- 列名关键词 -> 标准字段（最长优先匹配）----
COLUMN_KEYWORDS = {
    "voucher_no": ["凭证编号", "凭证字号", "凭证号", "字号"],
    "voucher_type": ["凭证类型", "凭证种类"],
    "period": ["记账期间", "会计期间", "期间"],
    "doc_date": ["记账日期", "凭证日期", "业务日期", "单据日期", "制单日期", "日期"],
    "line_no": ["行号", "序号", "分录号"],
    "summary": ["摘要", "业务摘要", "内容"],
    "account": ["会计科目", "科目名称", "科目"],
    "debit": ["借方金额", "借方", "借"],
    "credit": ["贷方金额", "贷方", "贷"],
    "cash_flow": ["现金流量", "现金流"],
    "status": ["凭证状态", "状态"],
    "maker": ["制单人", "制单", "填制"],
    "checker": ["审核人", "审核", "复核"],
    "booker": ["记账人", "记账"],
    "counterparty": ["交易对手", "供应商", "客户", "对方科目", "往来单位"],
    # 部门维度：真实序时账不一定有，没有时维度分析自动跳过并注明
    "department": ["使用部门", "核算部门", "成本中心", "部门", "事业部"],
}

# 从 摘要/会计科目 文本中抽取交易对手公司名（best-effort）
RE_LEDGER_CP = re.compile(
    r"(?:应付|应收|采购暂估|单位应付|单位应收|供应商|客户|往来单位)\s*\|?\s*"
    r"([一-龥A-Za-z0-9（）().\-]{2,30}?(?:有限公司|股份公司|集团|公司|厂))"
)

_PERIOD_RE = re.compile(r"^\s*\d{4}[-/年]\d{1,2}")
_VNO_RE = re.compile(r"[\(\（]?\s*[记收付转银][\)）]?|字|第|号|\d{4}[-/]\d{2}|-?\d{3,}")


def _clean_number(x: Any) -> float | None:
    """清洗金额/数字：去千分位、去空格、容错。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("，", "").replace(" ", "")
    if s in ("", "-", "—", "None", "nan", "NaN"):
        return None
    # 提取最后一个数字片段（应对 '￥1,000.00' 之类）
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def detect_header_row(df_raw: pd.DataFrame) -> int:
    """检测表头所在行：寻找含凭证编号/摘要等关键词的行。"""
    keywords = ["凭证编号", "摘要", "会计科目", "借方", "贷方"]
    for i in range(min(5, len(df_raw))):
        row_vals = [str(v) for v in df_raw.iloc[i].tolist()]
        hit = sum(any(k in v for k in keywords) for v in row_vals)
        if hit >= 2:
            return i
    return 0


def build_column_mapping(columns) -> dict[str, str]:
    """将原始列名映射到标准字段。返回 {原始列名: 标准字段}。"""
    assigned: dict[str, str] = {}
    used_std: set[str] = set()

    # 1) 关键词匹配（按关键词长度降序，长的优先）
    for std, kws in COLUMN_KEYWORDS.items():
        for col in columns:
            col_str = str(col)
            if col_str in assigned:
                continue
            for kw in sorted(kws, key=len, reverse=True):
                if kw in col_str:
                    assigned[col_str] = std
                    used_std.add(std)
                    break

    # 2) 样本值启发式：未映射的列，根据样本值推断 period / voucher_no
    for col in columns:
        col_str = str(col)
        if col_str in assigned:
            continue
        # 列名恰好是 "凭证" 且内容像期间 -> period
        if col_str == "凭证" and "period" not in used_std:
            assigned[col_str] = "period"
            used_std.add("period")
    return assigned


def _parse_date(x: Any):
    """把各种日期写法解析为 datetime.date；无法解析返回 None。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if hasattr(x, "date") and not isinstance(x, str):
        try:
            return x.date()
        except Exception:
            pass
    s = str(x).strip()
    if not s or s in ("-", "—", "None", "nan", "NaT"):
        return None
    # 中文日期写法归一：2023年10月12日 -> 2023-10-12
    s = re.sub(r"[年/.]", "-", s).replace("月", "-").replace("日", "").strip("-")
    ts = pd.to_datetime(s, errors="coerce", format="mixed")
    return None if pd.isna(ts) else ts.date()


def analyze_excel(path: str | Path) -> dict:
    """
    主入口：分析序时账 Excel。
    返回 dict: {
      "source": 路径,
      "sheet_name": ...,
      "n_rows": ...,
      "n_cols": ...,
      "raw_columns": [...],
      "column_mapping": {...},
      "records": [ {标准字段: 值} ... ],   # 逐行
      "vouchers": [ {voucher_no, period, ...debit_sum, credit_sum, ...} ],  # 按凭证号聚合
      "warnings": [...],
    }
    """
    path = str(path)
    warnings: list[str] = []
    # 用 with 确保文件句柄关闭：批量处理时若不关，文件多了会耗尽句柄
    with pd.ExcelFile(path) as xls:
        sheet_names = list(xls.sheet_names)
    sheet = sheet_names[0]
    df_raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    header_row = detect_header_row(df_raw)
    header = [str(c).strip() for c in df_raw.iloc[header_row].tolist()]
    data = df_raw.iloc[header_row + 1:].reset_index(drop=True)
    data.columns = header

    mapping = build_column_mapping(header)
    if "voucher_no" not in mapping.values():
        warnings.append("未自动识别到『凭证编号』列，匹配可能失败，请检查原文件列名。")
    if "debit" not in mapping.values() or "credit" not in mapping.values():
        warnings.append("未识别到借方/贷方金额列，金额核对将受限。")

    # 逆映射：标准字段 -> 原始列名
    std_to_raw = {v: k for k, v in mapping.items()}

    records: list[dict] = []
    for i, (_, row) in enumerate(data.iterrows()):
        rec: dict[str, Any] = {}
        for std in STANDARD_FIELDS:
            raw_col = std_to_raw.get(std)
            val = row.get(raw_col) if raw_col is not None else None
            if std in ("debit", "credit"):
                val = _clean_number(val)
            elif std == "doc_date":
                val = _parse_date(val)
            else:
                val = None if (val is None or (isinstance(val, float) and pd.isna(val))) else str(val).strip()
            rec[std] = val
        # 跳过全空行
        if all(rec.get(f) in (None, "") for f in STANDARD_FIELDS):
            continue
        # 有日期无期间时，用日期回填期间（YYYY-MM），便于按期间聚合
        if not rec.get("period") and rec.get("doc_date"):
            rec["period"] = rec["doc_date"].strftime("%Y-%m")
        # Excel 物理行号（1 起，含表头行）：报告要能指回原表第几行
        rec["source_row"] = header_row + i + 2
        records.append(rec)

    # 按凭证号聚合
    vouchers = _aggregate_vouchers(records)
    n_dated = sum(1 for r in records if r.get("doc_date"))

    return {
        "source": path,
        "sheet_name": sheet,
        "n_rows": len(records),
        "n_cols": len(header),
        "raw_columns": header,
        "column_mapping": mapping,
        "records": records,
        "vouchers": vouchers,
        "n_dated_rows": n_dated,
        "has_date_dim": n_dated > 0,
        "warnings": warnings,
    }


def _aggregate_vouchers(records: list[dict]) -> list[dict]:
    """按 voucher_no 聚合多行分录为一个凭证。"""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in records:
        vno = rec.get("voucher_no") or "__NO_VNO__"
        if vno not in groups:
            groups[vno] = []
            order.append(vno)
        groups[vno].append(rec)

    vouchers: list[dict] = []
    for vno in order:
        rows = groups[vno]
        r0 = rows[0]
        debit_sum = sum((r.get("debit") or 0.0) for r in rows)
        credit_sum = sum((r.get("credit") or 0.0) for r in rows)
        summaries = [r.get("summary") for r in rows if r.get("summary")]
        accounts = [r.get("account") for r in rows if r.get("account")]
        # 交易对手：从摘要/科目文本抽公司名，跨行去重（best-effort）
        cp: dict[str, int] = {}
        for text in summaries + accounts:
            for m in RE_LEDGER_CP.finditer(text or ""):
                name = re.sub(r"\s", "", m.group(1))
                if len(name) >= 4:
                    cp[name] = cp.get(name, 0) + 1
        counterparties = [c for c, _ in sorted(cp.items(), key=lambda kv: -kv[1])][:30]
        vouchers.append({
            "voucher_no": vno if vno != "__NO_VNO__" else None,
            "period": r0.get("period"),
            "doc_date": r0.get("doc_date"),
            "voucher_type": r0.get("voucher_type"),
            "line_count": len(rows),
            "debit_sum": round(debit_sum, 2),
            "credit_sum": round(credit_sum, 2),
            "summaries": summaries,
            "accounts": accounts,
            "counterparties": counterparties,
            "counterparty_count": len(counterparties),
            "status": r0.get("status"),
            "maker": r0.get("maker"),
            "checker": r0.get("checker"),
            # 凭证在原始 Excel 里占用的物理行号，供报告回溯
            "source_rows": [r.get("source_row") for r in rows if r.get("source_row")],
        })
    return vouchers


# ---- 凭证号归一化（用于跨源匹配）----
def normalize_voucher_key(vno: str | None):
    """
    从凭证号提取 (type_char, period, seq) 用于匹配。
    例: '(记)-2023-01-0001' -> ('记','2023-01','0001')
        '记字第0001号'        -> ('记', None, '0001')
    找不到则返回 (None, None, None)。
    """
    if not vno:
        return (None, None, None)
    s = str(vno).strip()
    # 类型字：记/收/付/转/银/现
    m_type = re.search(r"[记收付转银现]", s)
    vtype = m_type.group() if m_type else None
    # 期间：YYYY-MM 或 YYYY-MM-DD
    m_period = re.search(r"\d{4}[-/]\d{2}(?:[-/]\d{2})?", s)
    period = m_period.group()[:7] if m_period else None
    # 序号：末段连续数字（至少3位）
    seqs = re.findall(r"\d{3,}", s)
    seq = seqs[-1] if seqs else None
    return (vtype, period, seq)


if __name__ == "__main__":
    import sys
    res = analyze_excel(sys.argv[1] if len(sys.argv) > 1 else
                        "/root/uploads/1788011095541198983-副本202301-202412序时账.xlsx")
    print("Sheet:", res["sheet_name"], "| 行:", res["n_rows"], "| 列:", res["n_cols"])
    print("列名:", res["raw_columns"])
    print("字段映射:", res["column_mapping"])
    print("凭证(聚合)数量:", len(res["vouchers"]))
    print("前2个凭证:", res["vouchers"][:2])
    print("警告:", res["warnings"])
