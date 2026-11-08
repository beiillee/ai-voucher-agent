# -*- coding: utf-8 -*-
"""
生成带已知异常的演示序时账
==========================
产出：
  data/demo_ledger.xlsx     —— 12 个月、约 1800 行分录的脱敏序时账
  data/demo_labels.json     —— 注入的异常清单（标准答案）

用途：
  1) 演示风险分析功能（真实账簿不能公开，样例又太小看不出效果）
  2) 作为评测集：跑一遍分析，比对能否把注入的异常筛出来，
     从而给出「检出率」这类可量化指标，而不是只说"能检测异常"。

注入的异常（labels）：
  A 金额离群     差旅费 3 笔（正常 2k–9k，注入 21w / 38w）、咨询费 1 笔
  B 期末集中     12 月最后 3 个自然日集中入账（平时约 8%，注入后约 40%）
  C 重复交易     同期内同额同摘要 2 组、跨期同额 2 组
  D 结构突变     咨询费占总额比例自 10 月起由约 3% 升至约 14%
  E 周末入账     12 月有 4 笔落在周末

用法：
    python make_demo_ledger.py
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SEED = 20261108

rng = np.random.default_rng(SEED)

# ---- 业务模板：(摘要前缀, 借方科目, 贷方科目, 金额中位数, 金额离散度) ----
TEMPLATES = [
    ("差旅费报销", "差旅费", "银行存款", 4_200, 0.55),
    ("办公用品采购", "办公费", "应付账款", 1_800, 0.60),
    ("业务招待", "业务招待费", "银行存款", 3_600, 0.70),
    ("咨询服务费", "咨询费", "应付账款", 28_000, 0.50),
    ("运输费结算", "运输费", "应付账款", 9_500, 0.45),
    ("工资计提", "职工薪酬", "应付职工薪酬", 620_000, 0.06),
    ("计提折旧", "制造费用", "累计折旧", 186_000, 0.04),
    ("采购入库", "原材料", "应付账款", 340_000, 0.55),
    ("销售确认", "应收账款", "主营业务收入", 780_000, 0.50),
    ("支付货款", "应付账款", "银行存款", 210_000, 0.60),
]
WEIGHTS = np.array([0.20, 0.14, 0.11, 0.06, 0.08, 0.04, 0.03, 0.13, 0.13, 0.08])

SUPPLIERS = ["常州示例材料有限公司", "苏州示例精密制造有限公司", "宁波示例科技有限公司",
             "无锡示例包装有限公司", "杭州示例物流有限公司", "嘉兴示例服务有限公司"]
MAKERS = ["张A", "李B", "王C", "赵D"]
CHECKERS = ["陈E", "周F"]

rows: list[dict] = []
labels: dict = {"amount_outliers": [], "period_end_cluster": {}, "duplicates": [],
                "structure_shift": {}, "weekend_postings": []}


def add(vno: str, d: date, summary: str, account: str, debit: float, credit: float,
        maker: str = None, tag: str | None = None) -> None:
    rows.append({
        "凭证编号": vno,
        "记账日期": d,
        "记账期间": d.strftime("%Y-%m"),
        "摘要": summary,
        "会计科目": account,
        "借方金额": round(debit, 2),
        "贷方金额": round(credit, 2),
        "制单人": maker or MAKERS[int(rng.integers(0, len(MAKERS)))],
        "审核人": CHECKERS[int(rng.integers(0, len(CHECKERS)))],
        "_tag": tag,
    })


def month_days(y: int, m: int) -> int:
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (nxt - date(y, m, 1)).days


def _workdays(y: int, m: int, lo: int, hi: int, n: int) -> list[int]:
    """在 [lo, hi) 内抽取 n 个工作日。真实账簿一般不安排在周末记账，
    周末凭证只来自后面显式注入的案例。"""
    out: list[int] = []
    guard = 0
    while len(out) < n and guard < n * 60:
        guard += 1
        d = int(rng.integers(lo, hi))
        if date(y, m, min(d, month_days(y, m))).weekday() < 5:
            out.append(min(d, month_days(y, m)))
    return out


def gen_month(y: int, m: int, seq_start: int = 1) -> int:
    """生成一个月的常规凭证，返回下一个可用流水号。"""
    nd = month_days(y, m)
    n_v = int(rng.integers(38, 48))
    seq = seq_start

    # 12 月：45% 的凭证压到最后 3 个自然日（注入期末集中）
    if m == 12:
        n_end = int(n_v * 0.45)
        days = _workdays(y, m, 1, nd - 2, n_v - n_end) + \
               _workdays(y, m, nd - 2, nd + 1, n_end)
    else:
        days = _workdays(y, m, 1, nd + 1, n_v)

    for day in sorted(days):
        d = date(y, m, int(day))
        vno = f"(记)-{y}-{m:02d}-{seq:04d}"
        seq += 1
        t_idx = int(rng.choice(len(TEMPLATES), p=WEIGHTS))
        prefix, acc_d, acc_c, med, disp = TEMPLATES[t_idx]
        amt = float(med * np.exp(rng.normal(0, disp)))
        if prefix in ("采购入库", "销售确认", "支付货款", "工资计提", "计提折旧"):
            summ = prefix if prefix in ("工资计提", "计提折旧") else \
                f"{prefix}|{SUPPLIERS[int(rng.integers(0, len(SUPPLIERS)))]}"
        else:
            summ = f"{prefix}|{SUPPLIERS[int(rng.integers(0, len(SUPPLIERS)))]}"
        add(vno, d, summ, acc_d, amt, 0.0)
        add(vno, d, summ, acc_c, 0.0, amt)
    return seq


# --------------------------------------------------------------------------
# 1) 常规数据：全年 12 个月
# --------------------------------------------------------------------------
for m in range(1, 13):
    gen_month(2025, m)

# --------------------------------------------------------------------------
# 2) 注入 A：金额离群（差旅费 + 咨询费）
# --------------------------------------------------------------------------
outlier_specs = [
    (date(2025, 3, 14), "差旅费报销|常州示例材料有限公司", "差旅费", "银行存款", 210_000.0),
    (date(2025, 7, 22), "差旅费报销|苏州示例精密制造有限公司", "差旅费", "银行存款", 380_000.0),
    (date(2025, 9, 5), "咨询服务费|杭州示例物流有限公司", "咨询费", "应付账款", 960_000.0),
]
for i, (d, summ, acc_d, acc_c, amt) in enumerate(outlier_specs, 1):
    vno = f"(记)-{d.year}-{d.month:02d}-9{i:03d}"
    add(vno, d, summ, acc_d, amt, 0.0, tag="AMOUNT_OUTLIER")
    add(vno, d, summ, acc_c, 0.0, amt, tag="AMOUNT_OUTLIER")
    labels["amount_outliers"].append({
        "voucher_no": vno, "account": acc_d, "amount": amt,
        "note": f"{acc_d} 单笔 {amt:,.0f}，明显高于同类交易",
    })

# --------------------------------------------------------------------------
# 3) 注入 C：重复交易
# --------------------------------------------------------------------------
dup_specs = [
    # 同期内重复：2025-05 两笔完全相同的咨询服务费
    (date(2025, 5, 8), date(2025, 5, 19), "咨询服务费|宁波示例科技有限公司", "咨询费", "应付账款", 152_000.0, "same_period"),
    (date(2025, 8, 6), date(2025, 8, 27), "运输费结算|嘉兴示例服务有限公司", "运输费", "应付账款", 88_500.0, "same_period"),
    # 跨期重复：可能为周期性业务，等级应偏低
    (date(2025, 2, 25), date(2025, 3, 25), "办公用品采购|无锡示例包装有限公司", "办公费", "应付账款", 36_800.0, "cross_period"),
    (date(2025, 6, 18), date(2025, 7, 18), "业务招待|常州示例材料有限公司", "业务招待费", "银行存款", 22_400.0, "cross_period"),
]
for j, (d1, d2, summ, acc_d, acc_c, amt, kind) in enumerate(dup_specs, 1):
    for k, d in enumerate((d1, d2), 1):
        vno = f"(记)-{d.year}-{d.month:02d}-8{j}{k:02d}"
        add(vno, d, summ, acc_d, amt, 0.0, tag="DUPLICATE_TRANSACTION")
        add(vno, d, summ, acc_c, 0.0, amt, tag="DUPLICATE_TRANSACTION")
    labels["duplicates"].append({
        "amount": amt, "summary": summ, "kind": kind,
        "vouchers": [f"(记)-{d1.year}-{d1.month:02d}-8{j}01", f"(记)-{d2.year}-{d2.month:02d}-8{j}02"],
    })

# --------------------------------------------------------------------------
# 4) 注入 D：咨询费结构突变（10 月起占比抬升）
# --------------------------------------------------------------------------
for m in (10, 11, 12):
    for k in range(6):
        d = date(2025, m, min(6 + k * 4, month_days(2025, m)))
        if d.weekday() >= 5:  # 结构突变凭证安排在交易日，避免与周末入账案例混淆
            d = d - timedelta(days=d.weekday() - 4)
        amt = float(420_000 * np.exp(rng.normal(0, 0.25)))
        vno = f"(记)-{m:02d}-7{k:02d}"
        add(vno, d, f"专项咨询|{SUPPLIERS[k % len(SUPPLIERS)]}", "咨询费", amt, 0.0, tag="STRUCTURE_SHIFT")
        add(vno, d, f"专项咨询|{SUPPLIERS[k % len(SUPPLIERS)]}", "应付账款", 0.0, amt, tag="STRUCTURE_SHIFT")
labels["structure_shift"] = {
    "account": "咨询费", "from_period": "2025-10",
    "note": "咨询费自 10 月起每月新增 6 笔专项咨询，占总额比例由约 3% 升至约 14%",
}

# --------------------------------------------------------------------------
# 5) 注入 E：周末入账（12 月）
# --------------------------------------------------------------------------
weekend_days = [d for d in (date(2025, 12, 6), date(2025, 12, 7),
                            date(2025, 12, 13), date(2025, 12, 14))]
for i, d in enumerate(weekend_days, 1):
    amt = float(58_000 * np.exp(rng.normal(0, 0.3)))
    vno = f"(记)-12-6{i:02d}"
    add(vno, d, f"办公用品采购|{SUPPLIERS[i % len(SUPPLIERS)]}", "办公费", amt, 0.0, tag="WEEKEND_POSTING")
    add(vno, d, f"办公用品采购|{SUPPLIERS[i % len(SUPPLIERS)]}", "应付账款", 0.0, amt, tag="WEEKEND_POSTING")
    labels["weekend_postings"].append({"date": str(d), "voucher_no": vno, "amount": round(amt, 2)})

# --------------------------------------------------------------------------
# 6) 期末集中（12 月）统计，用于对账
# --------------------------------------------------------------------------
dec = [r for r in rows if r["记账期间"] == "2025-12"]
dec_end = [r for r in dec if r["记账日期"].day >= 29]
labels["period_end_cluster"] = {
    "period": "2025-12",
    "end_window": "29-31",
    "share": round(len(dec_end) / len(dec) * 100, 1) if dec else 0,
    "n_rows_period": len(dec),
    "n_rows_end_window": len(dec_end),
}

# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
df = pd.DataFrame(rows).drop(columns=["_tag"])
DATA.mkdir(parents=True, exist_ok=True)
out_xlsx = DATA / "demo_ledger.xlsx"
df.to_excel(out_xlsx, index=False)

summary = {
    "file": str(out_xlsx.name),
    "n_rows": len(df),
    "n_vouchers": df["凭证编号"].nunique(),
    "periods": sorted(df["记账期间"].unique().tolist()),
    "accounts": sorted(df["会计科目"].unique().tolist()),
    "labels": labels,
}
(DATA / "demo_labels.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"已生成 {out_xlsx}")
print(f"分录 {len(df)} 行 | 凭证 {df['凭证编号'].nunique()} 张 | 期间 {len(summary['periods'])} 个 | 科目 {len(summary['accounts'])} 个")
print(f"注入：金额离群 {len(labels['amount_outliers'])} 笔 | 重复 {len(labels['duplicates'])} 组 | "
      f"周末 {len(labels['weekend_postings'])} 笔 | 12 月期末窗口占比 {labels['period_end_cluster']['share']}%")
print(f"标注文件：{DATA / 'demo_labels.json'}")
