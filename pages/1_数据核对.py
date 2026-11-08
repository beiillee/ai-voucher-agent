# -*- coding: utf-8 -*-
"""数据核对页：多文件上传 → 批量核对 → 结果追溯与下载。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from ui_utils import (
    OUTPUT_DIR, ROOT, STATUS_CN, anomalies_dataframe, pairs_dataframe,
    save_upload, setup_page, sidebar,
)
from agent.batch import run_batch

setup_page("数据核对")
sidebar()

st.title("🔍 数据核对")
st.caption("把 PDF 会计凭证与 Excel 序时账逐笔对上，输出差异、未匹配明细与可下载报告")

with st.sidebar:
    st.divider()
    use_demo = st.toggle("使用内置演示数据", value=False,
                         help="加载 data/demo_recon 的 7 张凭证 PDF 与 8 张账内凭证")
    st.divider()
    st.caption("多份 PDF 会逐个独立处理，同名文件不会互相覆盖。")

# ---------------------------------------------------------------- 上传区
c1, c2 = st.columns(2)
with c1:
    pdf_files = st.file_uploader(
        "① 会计凭证 PDF（可多选）", type=["pdf"], accept_multiple_files=True,
        disabled=use_demo)
with c2:
    xlsx_file = st.file_uploader("② 序时账 Excel", type=["xlsx", "xls"],
                                 disabled=use_demo)

if use_demo:
    st.info("已加载内置演示数据：`data/demo_recon`（7 张 PDF 凭证 + 8 张账内凭证）")

ready = use_demo or (pdf_files and xlsx_file)
go = st.button("🚀 开始核对", type="primary", disabled=not ready)

if go:
    if use_demo:
        pdf_paths = [ROOT / "data" / "demo_recon" / "vouchers.pdf"]
        ledger_path = ROOT / "data" / "demo_recon" / "ledger.xlsx"
    else:
        pdf_paths = save_upload(pdf_files, "pdf")
        ledger_path = save_upload([xlsx_file], "ledger")[0]

    bar = st.progress(0.0, text="准备中…")

    def on_progress(done, total, name):
        bar.progress(done / total, text=f"已处理 {done}/{total}：{name}")

    with st.spinner("解析 PDF、读取序时账、匹配、异常检测、风险分析…"):
        try:
            result = run_batch(pdf_paths, ledger_path,
                               output_dir=OUTPUT_DIR / "web", on_progress=on_progress)
        except Exception as e:  # 整批失败（如 Excel 无法读取）
            st.error(f"运行失败：{type(e).__name__}: {e}")
            st.stop()
    bar.progress(1.0, text="完成")
    st.session_state["batch"] = result

result = st.session_state.get("batch")
if not result:
    st.info("请上传文件（或打开内置演示数据），然后点击「开始核对」。")
    st.stop()

s = result["summary"]

# ---------------------------------------------------------------- 概览
st.subheader("一、核对概览")
m = st.columns(6)
m[0].metric("文件数", f"{s['n_files_ok']}/{s['n_files']}", help="成功 / 总数")
m[1].metric("PDF 凭证", s["total_pdf"])
m[2].metric("已匹配", s["matched"])
m[3].metric("金额/日期不符", s["amount_mismatch"] + s["date_mismatch"])
m[4].metric("匹配率", f"{s['match_rate_pct']}%")
m[5].metric("账内未覆盖", s["ledger_only"], help="序时账中存在、本次 PDF 未扫描")

if s["n_files_failed"]:
    st.warning(f"有 {s['n_files_failed']} 个文件处理失败，详见下表")

with st.expander("逐文件处理情况", expanded=s["n_files_failed"] > 0):
    rows = []
    for f in result["files"]:
        fs = (f.get("reconciliation") or {}).get("summary") or {}
        rows.append({
            "文件": f["file"],
            "状态": "成功" if f["ok"] else "失败",
            "页数": f.get("n_pages"),
            "PDF凭证": f.get("n_pdf_vouchers"),
            "已匹配": fs.get("matched", 0),
            "金额不符": fs.get("amount_mismatch", 0),
            "日期不符": fs.get("date_mismatch", 0),
            "疑似": fs.get("suspected", 0),
            "仅PDF": fs.get("pdf_only", 0),
            "错误": f.get("error", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- 明细
st.subheader("二、逐笔核对结果")

df = pairs_dataframe(result["pairs"])
if df.empty:
    st.info("未提取到 PDF 凭证。")
else:
    f1, f2, f3 = st.columns([2, 2, 3])
    with f1:
        statuses = st.multiselect("按状态筛选", sorted(df["状态"].dropna().unique()))
    with f2:
        files = st.multiselect("按文件筛选", sorted(df["来源文件"].dropna().unique()))
    with f3:
        kw = st.text_input("凭证号 / 科目关键词", "")

    view = df.copy()
    if statuses:
        view = view[view["状态"].isin(statuses)]
    if files:
        view = view[view["来源文件"].isin(files)]
    if kw:
        mask = (view["PDF凭证号"].astype(str).str.contains(kw, na=False)
                | view["PDF科目"].astype(str).str.contains(kw, na=False)
                | view["账内凭证号"].astype(str).str.contains(kw, na=False))
        view = view[mask]

    st.caption(f"共 {len(view)} 条（全量 {len(df)} 条）")
    st.dataframe(view, use_container_width=True, hide_index=True, height=340)

# ---------------------------------------------------------------- 异常与未匹配
st.subheader("三、异常清单")
anoms = result["anomalies"]["anomalies"]
if anoms:
    adf = anomalies_dataframe(anoms)
    st.dataframe(adf, use_container_width=True, hide_index=True, height=260)
else:
    st.info("未发现异常。")

st.subheader("四、未匹配序时账（账有、PDF 未覆盖）")
unmatched = result["reconciliation"]["unmatched_ledger"]
if unmatched:
    rows = [{
        "凭证号": u.get("voucher_no"),
        "期间": u.get("period"),
        "借方": u.get("debit_sum"),
        "会计科目": "、".join((u.get("accounts") or [])[:4]),
        "摘要": "、".join((u.get("summaries") or [])[:2]),
        "原始行号": ",".join(str(x) for x in (u.get("source_rows") or [])),
    } for u in unmatched]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("这些凭证不在本次 PDF 扫描范围内，属信息性提示；行号可直接回原表核对。")
else:
    st.success("所有序时账凭证都有对应的 PDF 凭证。")

# ---------------------------------------------------------------- 下载
st.subheader("五、下载")
rp = Path(result["report_path"])
dl1, dl2 = st.columns(2)
with dl1:
    if rp.exists():
        st.download_button(
            "📥 核对报告（Excel，7 个 Sheet）", data=rp.read_bytes(),
            file_name="batch_reconciliation_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", use_container_width=True)
with dl2:
    if not df.empty:
        st.download_button(
            "📥 逐笔明细（CSV）",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name="pairs.csv", mime="text/csv", use_container_width=True)

st.caption(f"报告路径：`{rp}`")
