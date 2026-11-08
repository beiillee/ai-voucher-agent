# -*- coding: utf-8 -*-
"""风险分析页：只看序时账本身，输出带证据的风险线索与维度分析。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_utils import OUTPUT_DIR, ROOT, save_upload, setup_page, sidebar
from tools.excel_analyzer import analyze_excel
from tools.ledger_risk_analyzer import analyze_ledger_risk, LEVEL_WEIGHT

setup_page("风险分析")
sidebar()

st.title("📊 风险分析")
st.caption("不依赖 PDF，只审视序时账本身：金额离群、期末集中、重复入账、结构突变")

with st.sidebar:
    st.divider()
    use_demo = st.toggle("使用内置演示账", value=False,
                         help="data/demo_ledger.xlsx，1070 行、埋入 5 类已知异常")
    st.caption("风险线索是待核查提示，不是违规结论。")

xlsx = st.file_uploader("序时账 Excel", type=["xlsx", "xls"], disabled=use_demo)
if use_demo:
    st.info("已加载 `data/demo_ledger.xlsx`（1070 行演示账）")

go = st.button("📈 开始分析", type="primary", disabled=not (use_demo or xlsx))

if go:
    path = ROOT / "data" / "demo_ledger.xlsx" if use_demo else save_upload([xlsx], "risk")[0]
    with st.spinner("读取序时账并执行风险分析…"):
        try:
            res = analyze_excel(path)
            risk = analyze_ledger_risk(res["records"], res["vouchers"],
                                       has_date_dim=res.get("has_date_dim"))
        except Exception as e:
            st.error(f"分析失败：{type(e).__name__}: {e}")
            st.stop()
    st.session_state["risk"] = {"res": res, "risk": risk, "name": path.name}

pack = st.session_state.get("risk")
if not pack:
    st.info("上传序时账（或打开演示账），点击「开始分析」。")
    st.stop()

res, risk = pack["res"], pack["risk"]
score = risk.get("risk_score") or {}
lv = risk.get("by_level") or {}

# ---------------------------------------------------------------- 概览
st.subheader("一、总体情况")
c = st.columns(5)
c[0].metric("记录数", res["n_rows"])
c[1].metric("凭证数", len(res["vouchers"]))
c[2].metric("风险线索", risk["n_clues"])
c[3].metric("高 / 中 / 低", f"{lv.get('高',0)} / {lv.get('中',0)} / {lv.get('低',0)}")
c[4].metric("风险评分", score.get("score"), help=score.get("formula"))

with st.expander("评分口径（可复算）", expanded=False):
    st.code(score.get("formula", ""), language="text")
    st.write(f"加权原始分：**{score.get('raw_weight')}**　线索密度：**{score.get('density')}**")
    st.caption(score.get("disclaimer", ""))

cov = risk.get("coverage") or {}
st.caption(
    f"日期维度：{'日级' if cov.get('has_date_dim') else '无（已降级为月度分析）'}　｜　"
    f"金额比较组：{cov.get('amount_groups_evaluated', 0)} 个（每组样本下限 {cov.get('min_peer_n', 0)} 条）　｜　"
    f"结构分析口径：{cov.get('structure_mode') or cov.get('structure_scope') or '—'}"
)

# ---------------------------------------------------------------- 线索
st.subheader("二、风险线索")
clues = risk["clues"]
if not clues:
    st.success("未发现达到阈值的风险线索。")
else:
    lv_sel = st.multiselect("按等级筛选", ["高", "中", "低"], default=["高", "中"])
    type_cn = {
        "AMOUNT_OUTLIER": "金额离群", "PERIOD_END_CLUSTER": "期末集中入账",
        "WEEKEND_POSTING": "周末入账", "MONTH_SWING": "月度剧烈波动",
        "DUPLICATE_TRANSACTION": "重复交易", "SIMILAR_TRANSACTION": "疑似重复",
        "STRUCTURE_SHIFT": "结构变化",
    }
    types = st.multiselect("按类型筛选", list(type_cn.values()))
    sel = [c for c in clues if c["level"] in lv_sel
           and (not types or type_cn.get(c["type"], c["type"]) in types)]

    st.caption(f"共 {len(sel)} 条（全量 {len(clues)} 条）")
    rows = [{
        "等级": c["level"],
        "类型": type_cn.get(c["type"], c["type"]),
        "线索": c.get("title"),
        "科目": c.get("account"),
        "期间": c.get("period"),
        "凭证号": c.get("voucher_no"),
        "金额": c.get("amount"),
        "比较组": f"{c.get('peer_group','')}（{c.get('peer_n','')} 条）",
        "基线": c.get("baseline"),
        "偏离": c.get("deviation"),
        "方法": c.get("method"),
        "依据": c.get("reason"),
        "建议动作": c.get("next_step"),
        "证据": c.get("evidence"),
    } for c in sel]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=380)

    with st.expander("查看单条线索全文", expanded=False):
        idx = st.selectbox("选择线索", range(len(sel)),
                           format_func=lambda i: f"[{sel[i]['level']}] {sel[i].get('title')}")
        c = sel[idx]
        for k, label in [("baseline", "基线"), ("deviation", "偏离程度"),
                         ("method", "检测方法"), ("reason", "判断依据"),
                         ("next_step", "建议核查动作"), ("evidence", "证据定位")]:
            st.markdown(f"**{label}**：{c.get(k)}")

# ---------------------------------------------------------------- 维度
st.subheader("三、维度分析")
dim = risk.get("dimensions") or {}
tab1, tab2, tab3 = st.tabs(["科目", "月份", "部门"])

with tab1:
    acc = dim.get("by_account") or []
    if acc:
        adf = pd.DataFrame(acc).rename(columns={
            "account": "会计科目", "amount": "金额", "count": "笔数", "share_pct": "占比%"})
        st.bar_chart(adf.set_index("会计科目")["金额"].head(15))
        st.dataframe(adf, use_container_width=True, hide_index=True)
    else:
        st.info("未识别到会计科目列。")

with tab2:
    per = dim.get("by_period") or []
    if per:
        pdf_ = pd.DataFrame(per).rename(columns={
            "period": "期间", "debit": "借方", "credit": "贷方",
            "count": "笔数", "mom_pct": "环比%"})
        st.line_chart(pdf_.set_index("期间")[["借方", "贷方"]])
        st.dataframe(pdf_, use_container_width=True, hide_index=True)
    else:
        st.info("未识别到会计期间。")

with tab3:
    dep = dim.get("by_department") or []
    if dep:
        ddf = pd.DataFrame(dep).rename(columns={
            "department": "部门", "amount": "金额", "count": "笔数", "share_pct": "占比%"})
        st.dataframe(ddf, use_container_width=True, hide_index=True)
    else:
        st.info("原表未提供部门 / 成本中心列，该维度已跳过。")

# ---------------------------------------------------------------- 下载
st.subheader("四、导出")
out = pd.DataFrame([{
    "等级": c["level"], "类型": c["type"], "线索": c.get("title"),
    "科目": c.get("account"), "期间": c.get("period"), "凭证号": c.get("voucher_no"),
    "金额": c.get("amount"), "比较组": c.get("peer_group"), "样本数": c.get("peer_n"),
    "基线": c.get("baseline"), "偏离": c.get("deviation"), "方法": c.get("method"),
    "依据": c.get("reason"), "建议动作": c.get("next_step"), "证据": c.get("evidence"),
} for c in clues])
st.download_button("📥 风险线索（CSV）", data=out.to_csv(index=False).encode("utf-8-sig"),
                   file_name="risk_clues.csv", mime="text/csv")
