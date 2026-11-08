# -*- coding: utf-8 -*-
"""
AI 财务凭证智能核查与风险分析系统 —— 首页
==========================================
启动： streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from ui_utils import DISCLAIMER, setup_page, sidebar

setup_page("首页")
sidebar()

st.title("📒 AI 财务凭证智能核查与风险分析系统")
st.caption("账证核对 · 序时账风险分析 · 全程可追溯 · 统计异常不等于违规")

st.markdown(
    """
把 PDF 会计凭证与 Excel 序时账对上，回答三个问题：

1. **账里的每一笔，有没有凭证支撑？** —— 逐笔核对凭证号、期间、金额、科目
2. **凭证里的每一张，账里有没有入账？** —— 反向核对，找出账外或漏记
3. **账簿本身有没有值得看的迹象？** —— 金额离群、期末集中、重复入账、结构突变
"""
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("匹配状态", "6 类", help="已匹配 / 日期不符 / 金额不符 / 疑似 / PDF独有 / 编号未识别")
c2.metric("风险线索", "7 类", help="金额离群、期末集中、周末入账、月度波动、重复、疑似重复、结构变化")
c3.metric("报告", "7 个 Sheet", help="核对结果、异常清单、未匹配序时账、风险线索、风险概览、维度分析、统计汇总")
c4.metric("评估", "可复算", help="字段抽取、匹配、异常三类指标均由实际运行计算")

st.divider()

left, right = st.columns([1.15, 1])

with left:
    st.subheader("处理流程")
    st.markdown(
        """
```
PDF 凭证 ──► 有文字层? ──否──► 渲染 + OCR（chi_sim, 双 psm 融合）
                │是
                ▼
          逐页抽字段：凭证号 / 日期 / 单位 / 金额 / 科目 / 摘要
                │
                ▼
          跨页聚合：按凭证号归组，日期与金额取本凭证所辖页的众数
                │
                ▼              序时账 Excel
          账证匹配 ◄──────── 自动识别表头、列名映射、行号保留
                │
                ├──► 异常清单（对不上的地方）
                ├──► 未匹配序时账明细（可回溯到原表行号）
                └──► 风险线索（账簿自身，每条带基线/样本数/方法/建议动作）
```
"""
    )

with right:
    st.subheader("开始使用")
    st.markdown(
        f"""
- 🔍 **数据核对**：上传一份或多份凭证 PDF，加一份序时账
- 📊 **风险分析**：只上传序时账，看账簿本身的风险线索
- 🧪 **评估结果**：查看演示集上的字段/匹配/异常指标
- 📖 **使用说明**：安装、口径与常见问题
"""
    )
    if st.button("前往数据核对 →", type="primary", use_container_width=True):
        st.switch_page("pages/1_数据核对.py")

st.divider()

with st.expander("⚠️ 适用范围与合规声明（建议先读）", expanded=False):
    st.markdown(DISCLAIMER)

st.caption("© AI 财务凭证智能核查系统 · 所有结论仅供参考，须由专业人员复核")
