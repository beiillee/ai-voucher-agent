# -*- coding: utf-8 -*-
"""使用说明页：安装、运行、口径与常见问题。"""
from __future__ import annotations

import streamlit as st

from ui_utils import DISCLAIMER, setup_page, sidebar

setup_page("使用说明")
sidebar()

st.title("📖 使用说明")

st.subheader("一、安装")
st.code("""python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# OCR 需要系统装 Tesseract 与中文包
# Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-chi-sim
# macOS :  brew install tesseract tesseract-lang
""", language="bash")

st.subheader("二、三种运行方式")
st.code("""# 1) 网页版（推荐）
streamlit run app.py

# 2) 命令行
python3 main.py 凭证.pdf 序时账.xlsx output/

# 3) 评估（在演示集上跑指标）
python3 -m evaluation.run
""", language="bash")

st.subheader("三、匹配状态怎么读")
st.markdown("""
| 状态 | 含义 | 该怎么处理 |
|---|---|---|
| 已匹配 | 凭证号、期间、金额全部一致 | 抽查即可 |
| 日期不一致 | 凭证能对上，但期间不同 | 核对是否跨期入账 |
| 金额不一致 | 凭证能对上，但借贷金额有差异 | 核对原始凭证金额与账内金额 |
| 疑似匹配 | 多个候选同样合理，无法唯一确定 | **必须人工判断**，系统不下结论 |
| PDF有/账里无 | 凭证存在但账内未找到 | 检查是否漏记或账外凭证 |
| 凭证号未识别 | OCR 未识别出凭证号 | 检查扫描质量，或改为人工录入 |
""")

st.subheader("四、风险线索怎么读")
st.markdown("""
每条线索都包含六要素，缺一不可：

- **基线**：拿什么做比较（例如「同科目近 12 个月中位数」）
- **比较组与样本数**：和谁比、比了多少条
- **偏离程度**：差多少倍、多少个标准差
- **检测方法**：用了什么统计口径
- **判断依据**：为什么把它列出来
- **建议核查动作**：下一步该看什么

> 统计异常是**筛查线索**，不等于违规或舞弊。是否构成问题须由财务/审计人员判断。
""")

st.subheader("五、报告里有什么")
st.markdown("""
`batch_reconciliation_result.xlsx` 共 7 个 Sheet：

1. **核对结果** —— 逐笔比对，含科目、页码、账内行号、候选数
2. **异常清单** —— 对不上的地方，信息性条目单独标注
3. **未匹配序时账** —— 账里有、PDF 未覆盖的凭证明细（可回溯行号）
4. **风险线索** —— 每条带基线、样本数、方法、依据、建议动作、证据
5. **风险概览** —— 各类分析是否执行、是否降级，附方法论说明
6. **维度分析** —— 科目 / 月份 / 部门汇总与风险评分口径
7. **统计汇总** —— 各项计数与匹配率
""")

st.subheader("六、常见问题")
with st.expander("扫描件很慢怎么办？"):
    st.markdown("""
扫描件需要逐页渲染 + OCR，耗时随页数线性增长。可以：

- 只上传需要核查的凭证页
- 提高扫描清晰度、避免倾斜，OCR 置信度会明显上升
- 同一份 PDF 二次处理会命中缓存（`output/cache/`），不再重复 OCR
""")
with st.expander("为什么有的凭证金额对不上？"):
    st.markdown("""
先看「匹配方式」列。若是 P2（仅编号）或 P4（模糊），说明凭证号只匹配到了一部分，
期间或编号本身可能有问题。再看「PDF借方」与「账内借方」的差额是否为整数，
整数差额通常是漏记或多记，非整数差额更可能是 OCR 噪声。
""")
with st.expander("能保证不漏吗？"):
    st.markdown("""
不能。系统对**未匹配的数据全部保留明细**（不会静默丢弃），
但 OCR 对模糊扫描件的识别存在上限。演示集上的指标不代表真实扫描件效果，
重要业务请以人工复核为准。
""")

st.divider()
st.subheader("七、合规声明")
st.markdown(DISCLAIMER)
