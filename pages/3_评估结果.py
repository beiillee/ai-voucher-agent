# -*- coding: utf-8 -*-
"""评估结果页：展示在演示集上实际跑出来的指标，不做任何美化。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from ui_utils import ROOT, setup_page, sidebar

setup_page("评估结果")
sidebar()

st.title("🧪 评估结果")
st.caption("演示集上的实测指标。数字由 evaluation 模块真实运行得出，不是估算值")

EVAL_JSON = ROOT / "output" / "evaluation" / "evaluation.json"

if not EVAL_JSON.exists():
    st.warning("尚未生成评估结果。在项目根目录运行：")
    st.code("python3 -m evaluation.run", language="bash")
    st.stop()

data = json.loads(EVAL_JSON.read_text(encoding="utf-8"))
f, m, a, p = data["fields"], data["matching"], data["anomalies"], data["performance"]

st.caption(f"生成时间：{data['generated_at']}　｜　数据集：`{data['dataset']['name']}`")

c = st.columns(4)
c[0].metric("字段抽取准确率", f"{f['accuracy']*100:.1f}%", help=f"{f['correct']}/{f['total']} 个字段")
c[1].metric("匹配 F1", f"{m['f1']*100:.1f}%",
            help=f"P {m['precision']*100:.1f}% / R {m['recall']*100:.1f}%")
c[2].metric("异常 F1", f"{a['f1']*100:.1f}%",
            help=f"P {a['precision']*100:.1f}% / R {a['recall']*100:.1f}%")
c[3].metric("凭证级误报率", f"{a['false_positive_rate']*100:.1f}%")

st.info("演示集是程序生成的回归基准，指标用于防止改动引入退化，不代表真实业务分布。")

tab1, tab2, tab3, tab4 = st.tabs(["字段抽取", "账证匹配", "异常检测", "性能"])

with tab1:
    rows = [{"字段": k, "正确": v["ok"], "总数": v["n"],
             "准确率": f"{v['accuracy']*100:.1f}%"} for k, v in f["by_field"].items()]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("逐条明细"):
        st.dataframe(pd.DataFrame(f["details"]), use_container_width=True, hide_index=True)

with tab2:
    st.markdown(f"""
- **Precision** {m['precision']*100:.1f}%　**Recall** {m['recall']*100:.1f}%　**F1** {m['f1']*100:.1f}%
- 状态判定准确率（找对对象后金额/日期结论也对）：**{m['status_accuracy']*100:.1f}%**
- TP / FP / FN / TN = {m['tp']} / {m['fp']} / {m['fn']} / {m['tn']}
""")
    st.dataframe(pd.DataFrame(m["details"]), use_container_width=True, hide_index=True)

with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**标准答案事件**")
        st.write(a["truth_events"] or "无")
        st.markdown("**实际报出**")
        st.write(a["predicted_events"] or "无")
    with c2:
        st.markdown("**误报**")
        st.write(a["fp_events"] or "无")
        st.markdown("**漏报**")
        st.write(a["fn_events"] or "无")

with tab4:
    st.markdown(f"""
- 端到端耗时（{p['repeats']} 次中位数）：**{p['e2e_seconds_median']} s**（区间 {p['e2e_seconds_min']} – {p['e2e_seconds_max']} s）
- 峰值内存：**{p['peak_memory_mb']} MB**
- 批量：{p['batch_files']} 个文件，成功 {p['batch_ok']}，失败 {p['batch_failed']}，失败率 {p['batch_failure_rate']*100:.1f}%
- 批量耗时：{p['batch_seconds']} s
""")
    st.caption("演示 PDF 含文字层，不触发 OCR；扫描件耗时按页数线性增加。")

md = ROOT / "output" / "evaluation" / "evaluation.md"
if md.exists():
    st.download_button("📥 下载评估报告（Markdown）", data=md.read_bytes(),
                       file_name="evaluation.md", mime="text/markdown")
