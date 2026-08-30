# -*- coding: utf-8 -*-
"""
AI 财务凭证智能核对 Agent - Streamlit 前端
=========================================
启动： streamlit run app.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

# 让 tools / agent 包可被导入
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.orchestrator import run as run_agent  # noqa: E402

st.set_page_config(
    page_title="AI 财务凭证智能核对 Agent",
    page_icon="📒",
    layout="wide",
)

st.title("📒 AI 财务凭证智能核对 Agent")
st.caption("AI Financial Voucher Reconciliation Agent · 仅作财务资料智能核对辅助工具")

# ---- 合规声明 ----
with st.expander("⚠️ 适用范围与合规声明（必读）", expanded=False):
    st.markdown("""
- 本工具是 **财务资料智能核对辅助工具**，**不**声称能"自动完成审计"、"自动判断财务造假"、"替代审计人员"。
- 所有 AI 输出仅作为辅助线索，**最终判断须由具备资质的财务/审计人员**做出。
- 输出 Excel 报告保留：原始数据、提取结果、匹配依据、异常原因、confidence，确保可追溯。
""")

# ---- 侧边栏：文件上传 ----
with st.sidebar:
    st.header("📤 上传文件")
    pdf_file = st.file_uploader("① 会计凭证 PDF", type=["pdf"])
    xlsx_file = st.file_uploader("② 序时账 Excel", type=["xlsx", "xls"])
    st.divider()
    use_llm = st.checkbox("启用 LLM 异常解释（可选，需 .env 配置 OPENAI_API_KEY）", value=False)
    go = st.button("🚀 开始智能核对", type="primary", use_container_width=True,
                   disabled=not (pdf_file and xlsx_file))

# ---- 主区 ----
if go and pdf_file and xlsx_file:
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, pdf_file.name)
        xlsx_path = os.path.join(tmp, xlsx_file.name)
        with open(pdf_path, "wb") as f: f.write(pdf_file.getbuffer())
        with open(xlsx_path, "wb") as f: f.write(xlsx_file.getbuffer())

        out_dir = os.path.join(ROOT, "output")
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        with st.spinner("🔄 正在解析 PDF、执行 OCR、读取 Excel、匹配、异常检测..."):
            try:
                result = run_agent(pdf_path, xlsx_path, output_dir=out_dir, use_llm=use_llm)
            except Exception as e:
                st.error(f"运行失败：{e}")
                st.stop()

        st.success("✅ 核对完成")

        # 1) 文件解析情况
        st.subheader("📂 1. 文件解析情况")
        c1, c2 = st.columns(2)
        with c1:
            pdf_meta = result["pdf"]
            st.markdown(f"""
**PDF**：`{Path(pdf_meta['source']).name}`
- 总页数：**{pdf_meta['n_pages']}**
- 是否含文字层：**{'是' if pdf_meta['has_text_layer'] else '否'}**
- 是否调用 OCR：**{'是' if pdf_meta['used_ocr'] else '否'}**
- 警告：{pdf_meta.get('warnings') or '无'}
""")
        with c2:
            led = result["ledger"]
            st.markdown(f"""
**序时账**：`{Path(led['source']).name}`
- Sheet：{led['sheet_name']}
- 行数 / 列数：{led['n_rows']} / {led['n_cols']}
- 凭证数（聚合）：{led['n_vouchers']}
- 字段映射：{len(led['column_mapping'])} 个
- 警告：{led.get('warnings') or '无'}
""")

        # 2) 统计概览
        st.subheader("📊 2. 核对概览")
        s = result["reconciliation"]["summary"]
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("PDF 凭证数", s["total_pdf"])
        m2.metric("序时账凭证数", s["total_ledger"])
        m3.metric("成功匹配", s["matched"], delta=None)
        m4.metric("异常（非信息）", result["anomalies"]["n_anomalies"])
        m5.metric("匹配率", f"{s['match_rate_pct']}%")
        m6.metric("LEDGER_ONLY(信息)", s["ledger_only"])

        # 3) 异常列表
        st.subheader("⚠️ 3. 异常清单")
        anom = result["anomalies"]["anomalies"]
        if anom:
            import pandas as pd
            df_anom = pd.DataFrame(anom)
            st.dataframe(df_anom, use_container_width=True, height=300)
        else:
            st.info("未发现异常。")

        # 4) 逐笔核对结果
        st.subheader("🔍 4. 逐笔核对结果（PDF 凭证）")
        pairs = result["reconciliation"]["pairs"]
        if pairs:
            import pandas as pd
            rows = []
            for p in pairs:
                pv = p["pdf_voucher"]; lv = p.get("ledger_voucher") or {}
                rows.append({
                    "PDF 凭证号": pv.get("voucher_no"),
                    "PDF 日期": pv.get("date"),
                    "PDF 借方": pv.get("debit_total"),
                    "序时账 凭证号": lv.get("voucher_no"),
                    "序时账 期间": lv.get("period"),
                    "序时账 借方": lv.get("debit_sum"),
                    "借方差异": p.get("debit_diff"),
                    "匹配状态": p.get("match_status"),
                    "匹配方式": p.get("match_method"),
                    "置信度": p.get("match_confidence"),
                    "OCR 置信度": pv.get("ocr_confidence_avg"),
                    "异常原因": p.get("anomaly_reason") or "",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=320)

            # LLM 解释（若启用且有）
            if result.get("llm_used") and result.get("llm_explanations"):
                with st.expander("🤖 LLM 异常解释（自然语言）", expanded=False):
                    for idx, exp in result["llm_explanations"].items():
                        st.markdown(f"**PDF 凭证 {pairs[idx]['pdf_voucher'].get('voucher_no')}**：{exp}")
        else:
            st.info("未提取到 PDF 凭证。")

        # 5) 下载
        st.subheader("📥 5. 下载核查报告")
        report_path = result["report_path"]
        if os.path.exists(report_path):
            with open(report_path, "rb") as f:
                st.download_button(
                    "下载 voucher_reconciliation_result.xlsx",
                    data=f.read(),
                    file_name="voucher_reconciliation_result.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )
        st.caption(f"报告路径：`{report_path}`")

else:
    st.info("请在左侧上传 ① 会计凭证 PDF 与 ② 序时账 Excel，然后点击「开始智能核对」。")

# 页脚
st.divider()
st.caption("© AI Voucher Reconciliation Agent · 仅供辅助核查使用")
