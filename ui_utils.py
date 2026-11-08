# -*- coding: utf-8 -*-
"""
Streamlit 共享工具
==================
页面之间共用的路径处理、样式与展示函数。放在根目录而不是 pages/ 下，
避免 Streamlit 把工具模块误当成页面。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "output"

STATUS_CN = {
    "MATCHED": "已匹配",
    "DATE_MISMATCH": "日期不一致",
    "AMOUNT_MISMATCH": "金额不一致",
    "SUSPECTED": "疑似匹配",
    "PDF_ONLY": "PDF有/账里无",
    "OCR_UNCERTAIN": "凭证号未识别",
}

DISCLAIMER = """
- 本工具是**财务资料核对辅助工具**，不声称能自动完成审计、自动判定财务造假或替代审计人员。
- 所有输出均为**待核查线索**，最终判断须由具备资质的财务/审计人员作出。
- 金额是否一致由精确比较决定，模型只负责文字解释，不参与任何数值判断。
"""


def setup_page(title: str) -> None:
    st.set_page_config(page_title=f"{title} · 凭证核查系统", page_icon="📒", layout="wide")


def sidebar(active: str = "") -> None:
    with st.sidebar:
        st.markdown("### 📒 AI 凭证核查系统")
        st.caption("账证核对 · 风险分析 · 结果可追溯")
        st.divider()
        st.page_link("app.py", label="首页", icon="🏠")
        st.page_link("pages/1_数据核对.py", label="数据核对", icon="🔍")
        st.page_link("pages/2_风险分析.py", label="风险分析", icon="📊")
        st.page_link("pages/3_评估结果.py", label="评估结果", icon="🧪")
        st.page_link("pages/4_使用说明.py", label="使用说明", icon="📖")
        st.divider()
        st.caption("统计异常 ≠ 违规。请结合原始凭证判断。")


def save_upload(files, subdir: str) -> list[Path]:
    """
    把上传文件落到磁盘。

    每个文件放进独立子目录，避免同名文件互相覆盖 —— 多文件上传里
    两个都叫「凭证.pdf」是常事，直接平铺保存会让后一个盖掉前一个。
    """
    base = OUTPUT_DIR / "uploads" / subdir
    base.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    used: set[str] = set()
    for f in files:
        stem, suffix = Path(f.name).stem, Path(f.name).suffix
        name = f.name
        i = 1
        while name in used:                      # 同名时加序号，不覆盖
            name = f"{stem}_{i}{suffix}"
            i += 1
        used.add(name)
        d = base / Path(name).stem
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(f.getbuffer())
        paths.append(p)
    return paths


def pairs_dataframe(pairs: list[dict]) -> pd.DataFrame:
    rows = []
    for p in pairs:
        pv = p.get("pdf_voucher") or {}
        lv = p.get("ledger_voucher") or {}
        rows.append({
            "来源文件": p.get("source_file", ""),
            "PDF凭证号": pv.get("voucher_no"),
            "PDF日期": pv.get("date"),
            "PDF借方": pv.get("debit_total"),
            "PDF科目": "、".join(pv.get("accounts") or []),
            "账内凭证号": lv.get("voucher_no"),
            "账内期间": lv.get("period"),
            "账内借方": lv.get("debit_sum"),
            "账内行号": ",".join(str(x) for x in (lv.get("source_rows") or [])),
            "借方差异": p.get("debit_diff"),
            "状态": STATUS_CN.get(p.get("match_status"), p.get("match_status")),
            "匹配方式": p.get("match_method"),
            "候选数": p.get("candidate_count"),
            "说明": p.get("anomaly_reason") or "",
        })
    return pd.DataFrame(rows)


def anomalies_dataframe(anoms: list[dict]) -> pd.DataFrame:
    rows = [{
        "类型": a.get("type"),
        "凭证号": a.get("voucher_no"),
        "页码": a.get("page"),
        "详情": a.get("detail"),
        "信息性": "是" if a.get("is_info") else "",
    } for a in anoms]
    return pd.DataFrame(rows)
