# -*- coding: utf-8 -*-
"""
Ledger Risk Analyzer —— 序时账风险分析
=====================================
与「账证核对」不同，本模块不看 PDF，只审视序时账本身，
筛出值得进一步核查的记录（风险线索），而不是判定违规。

设计要点（对应方法论约束）：
  1. 「金额大」不等于「异常」。所有金额类判断都在**比较组**内进行，
     比较组按（会计科目 + 凭证类型）定义，不同业务性质的金额不混在一起比。
  2. 「统计异常」不等于「财务违规」。输出的是排序后的核查线索，
     每条都附：比较组、基线、偏离度、所用方法、建议核查动作、证据定位。
  3. 样本不足时宁可不出结论。组内样本 < MIN_PEER_N 时跳过并记录原因，
     不为了凑数把全院金额放在一起比。

四类分析：
  A. 金额离群   IQR 与 MAD 双法交叉，两法同时命中才判高等级
  B. 时间异常   期末集中入账 / 周末入账 / 月度剧烈波动
  C. 重复交易   金额 + 摘要 + 对手方 相同，且落在不同凭证
  D. 结构变化   科目占期间总额比例的变化

无日期的账簿自动降级：只做月度维度分析，并在 coverage 中说明。
"""
from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np

# ---- 可调参数（集中在此，便于说明与复核）----
MIN_PEER_N = 8          # 比较组内最少样本数，低于此不出金额结论
IQR_K_WATCH = 1.5       # IQR 观察阈值
IQR_K_ALERT = 3.0       # IQR 强阈值
MAD_K = 3.5             # 稳健 z 分数阈值
PERIOD_END_DAYS = 3     # 「期末」取当月最后 N 个自然日
PERIOD_END_MULT = 3.0   # 期末占比超过期望倍数才提示
WEEKEND_BASE_MAX = 0.05 # 全账簿周末入账占比低于此值，才认为本账簿制度上不周末入账
DUP_MIN_AMOUNT = 1000.0 # 低于此金额的重复不入线索（避免大量小额同质交易）
STRUCT_SHIFT_PP = 5.0   # 结构占比变动百分点的阈值
MIN_PERIOD_RECORDS = 20 # 单期间记录数下限，低于此不做结构比较（样本太少易出噪声）
MAX_CLUES_PER_TYPE = 40 # 单类型线索上限，按偏离强度取前 N
SIM_SIMILAR_MIN = 0.75  # 同额交易摘要相似度达到此值，报「疑似重复」
SIM_MAX_BUCKET = 40     # 单个金额桶超过此条数则跳过两两比较，防 O(n²) 拖慢

# 结算类 / 对方科目：它们的金额是业务发生的结果，不是业务本身。
# 例如银行存款在付款凭证里永远是对方科目，金额跨度可从数千到数百万，
# 把它和其它科目放在同一个比较组里比较没有业务含义。这类科目不参与
# 金额离群、结构占比、环比波动分析，但仍参与账证核对与重复交易识别。
SETTLEMENT_KEYWORDS = (
    "银行存款", "库存现金", "现金", "应付账款", "应收账款", "应付票据", "应收票据",
    "应付职工薪酬", "累计折旧", "累计摊销", "应交税费", "预付账款", "预收账款",
    "其他应付款", "其他应收款", "内部往来",
)


def _is_settlement(account: str | None) -> bool:
    if not account:
        return False
    return any(k in str(account) for k in SETTLEMENT_KEYWORDS)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def _amt(rec: dict) -> float:
    """取该分录的非零方金额。一笔分录只算一次，避免借贷两行重复计数。"""
    d = rec.get("debit") or 0.0
    c = rec.get("credit") or 0.0
    if d > 0:
        return float(d)
    if c > 0:
        return float(c)
    return 0.0


def _norm_text(s: Any) -> str:
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，,。.、；;：:（）()\[\]【】\-—_/]", "", s)
    return s


def _period_key(rec: dict) -> str:
    p = rec.get("period")
    if p:
        return str(p).strip()[:7]
    d = rec.get("doc_date")
    return d.strftime("%Y-%m") if d else "未知期间"


def _fmt_money(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:,.2f}"


def _clue(**kw) -> dict:
    base = {
        "type": "", "level": "", "title": "", "account": "", "period": "",
        "voucher_no": "", "amount": None, "peer_group": "", "peer_n": 0,
        "baseline": "", "deviation": "", "method": "", "reason": "",
        "next_step": "", "evidence": "",
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# A. 金额离群（IQR + MAD 双法，比较组内比较）
# --------------------------------------------------------------------------
def _amount_outliers(records: list[dict], coverage: dict) -> list[dict]:
    # 按（科目 + 凭证类型）建比较组；结算类科目不参与
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        a = _amt(r)
        if a <= 0:
            continue
        if not r.get("account"):
            continue
        if _is_settlement(r.get("account")):
            continue
        groups[(str(r["account"]).strip(), str(r.get("voucher_type") or "").strip())].append(r)

    # 组内样本不足时退化为「仅按科目」分组，仍不足则放弃
    merged: dict[tuple, list[dict]] = defaultdict(list)
    for (acc, vtype), rows in groups.items():
        if len(rows) >= MIN_PEER_N:
            merged[(acc, vtype)].extend(rows)
            continue
        # 找同科目的其它组合并
        same = [rs for (a2, _), rs in groups.items() if a2 == acc]
        pool = [r for rs in same for r in rs]
        if len(pool) >= MIN_PEER_N:
            merged[(acc, "")].extend(pool)

    clues: list[dict] = []
    evaluated = 0
    skipped = []
    for (acc, vtype), rows in groups.items():
        if len(rows) < MIN_PEER_N:
            same = sum(len(rs) for (a2, _), rs in groups.items() if a2 == acc)
            if same < MIN_PEER_N:
                skipped.append(f"{acc}（样本 {same} 条）")

    for (acc, vtype), rows in merged.items():
        vals = np.array([_amt(r) for r in rows], dtype=float)
        if vals.size < MIN_PEER_N:
            continue
        evaluated += 1
        q1, q3 = np.percentile(vals, [25, 75])
        iqr = q3 - q1
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        if iqr <= 0 and mad <= 0:
            continue  # 组内金额几乎相同，无从判定离群
        upper_watch = q3 + IQR_K_WATCH * iqr if iqr > 0 else None
        upper_alert = q3 + IQR_K_ALERT * iqr if iqr > 0 else None
        robust_scale = 1.4826 * mad if mad > 0 else None

        group_label = f"{acc}" + (f" / {vtype}" if vtype else "")
        for r in rows:
            x = _amt(r)
            hit_iqr_alert = upper_alert is not None and x > upper_alert
            hit_iqr_watch = upper_watch is not None and x > upper_watch
            rz = (x - med) / robust_scale if robust_scale else 0.0
            hit_mad = robust_scale is not None and rz > MAD_K
            if not (hit_iqr_watch or hit_mad):
                continue

            if hit_iqr_alert and hit_mad:
                level = "高"
            elif hit_iqr_watch and hit_mad:
                level = "中"
            else:
                level = "低"

            multiple = (x / med) if med > 0 else None
            methods = []
            if hit_iqr_watch:
                methods.append(f"IQR(k={IQR_K_ALERT if hit_iqr_alert else IQR_K_WATCH})")
            if hit_mad:
                methods.append(f"MAD(稳健z={rz:.1f})")
            baseline = (
                f"比较组「{group_label}」共 {len(rows)} 条：中位数 {_fmt_money(med)}，"
                f"Q1 {_fmt_money(q1)}，Q3 {_fmt_money(q3)}"
            )
            dev_parts = []
            if multiple:
                dev_parts.append(f"为中位数 {multiple:.1f} 倍")
            if upper_watch is not None:
                dev_parts.append(f"观察上界 {_fmt_money(upper_watch)}")
            if upper_alert is not None:
                dev_parts.append(f"强上界 {_fmt_money(upper_alert)}")
            clues.append(_clue(
                type="AMOUNT_OUTLIER",
                level=level,
                title=f"{acc} 单笔金额显著高于同类交易",
                account=acc,
                period=_period_key(r),
                voucher_no=r.get("voucher_no") or "",
                amount=round(x, 2),
                peer_group=group_label,
                peer_n=len(rows),
                baseline=baseline,
                deviation="，".join(dev_parts),
                method=" + ".join(methods),
                reason=(
                    f"该笔金额 {_fmt_money(x)} 超出同比较组的常规分布。"
                    "金额大本身不代表有问题，仅说明与同类交易不一致，需核查业务实质。"
                ),
                next_step=(
                    "调阅该笔凭证及附件：核对合同/验收单/发票与付款审批链条；"
                    "与同科目近期同类交易比较单价与数量，确认金额构成是否合理。"
                ),
                evidence=f"凭证 {r.get('voucher_no') or '(无号)'}，期间 {_period_key(r)}，科目 {acc}",
                _score=(multiple or 0),
            ))

    coverage["amount_groups_evaluated"] = evaluated
    coverage["amount_groups_skipped"] = skipped[:10]
    return clues


# --------------------------------------------------------------------------
# B. 时间异常
# --------------------------------------------------------------------------
def _temporal_anomalies(records: list[dict], has_date: bool, coverage: dict) -> list[dict]:
    clues: list[dict] = []

    # B1/B2：依赖日级日期
    if has_date:
        by_period: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            if r.get("doc_date"):
                by_period[_period_key(r)].append(r)

        for period, rows in sorted(by_period.items()):
            if len(rows) < 10:
                continue
            try:
                y, m = int(period[:4]), int(period[5:7])
                month_days = calendar.monthrange(y, m)[1]
            except Exception:
                continue
            cutoff_day = month_days - PERIOD_END_DAYS + 1
            end_rows = [r for r in rows if r["doc_date"].day >= cutoff_day]
            share = len(end_rows) / len(rows)
            expected = PERIOD_END_DAYS / month_days
            if share > expected * PERIOD_END_MULT and len(end_rows) >= 5:
                amt = sum(_amt(r) for r in end_rows)
                clues.append(_clue(
                    type="PERIOD_END_CLUSTER",
                    level="中" if share > expected * 5 else "低",
                    title=f"{period} 期末 {PERIOD_END_DAYS} 日入账集中",
                    period=period,
                    amount=round(amt, 2),
                    peer_group=f"{period} 全部分录",
                    peer_n=len(rows),
                    baseline=(
                        f"该期间共 {len(rows)} 条分录，日均 {len(rows)/month_days:.1f} 条；"
                        f"期末 {PERIOD_END_DAYS} 日按均匀分布应占 {expected*100:.1f}%"
                    ),
                    deviation=f"实际占 {share*100:.1f}%，为期望的 {share/expected:.1f} 倍",
                    method="期末占比 vs 均匀分布期望",
                    reason="期末集中入账本身常见（结账、计提），但异常集中可能意味着跨期调节或补记交易。",
                    next_step=(
                        "抽取期末最后三日入账的凭证，检查是否涉及收入确认时点、费用跨期；"
                        "对期末大额计提关注期后是否冲回。"
                    ),
                    evidence=f"期间 {period}，期末 {PERIOD_END_DAYS} 日内 {len(end_rows)} 条",
                    _score=share / expected,
                ))

        # 周末入账：与该账簿自身的周末基线比较，而不是用固定的绝对阈值。
        # 真实账簿大多只安排工作日记账，若某期间周末入账明显偏离本账簿习惯，
        # 则可能存在补记或倒签。
        dated = [r for r in records if r.get("doc_date")]
        if dated:
            period_total: dict[str, int] = defaultdict(int)
            period_weekend: dict[str, list[dict]] = defaultdict(list)
            for r in dated:
                p = _period_key(r)
                period_total[p] += 1
                if r["doc_date"].weekday() >= 5:
                    period_weekend[p].append(r)
            weekend_all = sum(len(v) for v in period_weekend.values())
            base = weekend_all / len(dated)
            for period, rows in sorted(period_weekend.items()):
                if len(rows) < 3 or not period_total[period]:
                    continue
                share = len(rows) / period_total[period]
                # 账簿本身基本不周末记账（基线极低）时，任何成规模的周末入账都值得看；
                # 否则要求明显高于本账簿基线。
                if base < 0.02:
                    flagged = len(rows) >= 3
                else:
                    flagged = share > max(2 * base, 0.15)
                if not flagged:
                    continue
                clues.append(_clue(
                    type="WEEKEND_POSTING",
                    level="中" if share > 0.30 else "低",
                    title=f"{period} 周末入账比例偏高",
                    period=period,
                    amount=round(sum(_amt(r) for r in rows), 2),
                    peer_group="全账簿日期分布",
                    peer_n=len(dated),
                    baseline=(
                        f"全账簿周末入账占比 {base*100:.1f}%；"
                        f"该期间共 {period_total[period]} 条分录"
                    ),
                    deviation=f"该期间周末入账 {len(rows)} 条，占 {share*100:.1f}%",
                    method="期间周末占比 vs 本账簿基线",
                    reason="非工作日记账与常规流程不符，可能是事后补记、倒签或系统时间异常。",
                    next_step=(
                        "核对周末入账凭证的制单与审核时间是否为系统时间；"
                        "抽查其附件日期与业务发生时间是否一致。"
                    ),
                    evidence=f"期间 {period}，周末凭证："
                             + "、".join(sorted({str(r.get("voucher_no") or "") for r in rows})[:5]),
                    _score=len(rows),
                ))
        coverage["temporal_mode"] = "日级（含期末集中与周末入账）"
    else:
        coverage["temporal_mode"] = "降级为月度（原表无日级日期列）"

    # B3：月度剧烈波动（不依赖日级日期）
    by_acc_period: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in records:
        if not r.get("account") or _is_settlement(r.get("account")):
            continue
        by_acc_period[str(r["account"]).strip()][_period_key(r)] += _amt(r)
    for acc, series in by_acc_period.items():
        if len(series) < 3:
            continue
        periods = sorted(series)
        vals = [series[p] for p in periods]
        med = float(np.median([v for v in vals if v > 0])) if any(v > 0 for v in vals) else 0
        if med <= 0:
            continue
        for i in range(1, len(periods)):
            prev, cur = vals[i - 1], vals[i]
            if prev <= 0 or cur <= 0:
                continue
            chg = (cur - prev) / prev
            if abs(chg) > 2.0 and max(prev, cur) > med:
                clues.append(_clue(
                    type="MONTH_SWING",
                    level="中" if abs(chg) > 4 else "低",
                    title=f"{acc} 金额环比{'激增' if chg > 0 else '骤降'}",
                    account=acc,
                    period=periods[i],
                    amount=round(cur, 2),
                    peer_group=f"{acc} 各期间",
                    peer_n=len(series),
                    baseline=f"该科目期间中位金额 {_fmt_money(med)}；上期 {_fmt_money(prev)}",
                    deviation=f"环比 {chg*100:+.0f}%",
                    method="科目×期间金额环比",
                    reason="单期金额突变可能来自业务量变化，也可能是科目归集口径调整或跨期结转。",
                    next_step="对比该科目前后两期明细，确认是业务量变化、口径调整还是跨期结转。",
                    evidence=f"{acc}，{periods[i-1]} → {periods[i]}",
                    _score=abs(chg),
                ))
    return clues


# --------------------------------------------------------------------------
# C. 重复交易
# --------------------------------------------------------------------------
def _duplicate_transactions(records: list[dict], coverage: dict) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        a = _amt(r)
        if a < DUP_MIN_AMOUNT:
            continue
        summ = _norm_text(r.get("summary"))
        cp = _norm_text(r.get("counterparty"))
        if not summ and not cp:
            continue
        key = (round(a, 2), summ, cp)
        buckets[key].append(r)

    clues: list[dict] = []
    checked = 0
    for key, rows in buckets.items():
        vnos = {str(r.get("voucher_no") or "") for r in rows}
        if len(vnos) < 2:
            continue
        checked += 1
        periods = sorted({_period_key(r) for r in rows})
        same_period = len(periods) == 1
        amount, summ, cp = key
        # 科目只展示业务科目，结算类科目（应付账款/银行存款）作为对方科目不单列
        accs = sorted({str(r.get("account") or "") for r in rows})
        biz = [a for a in accs if a and not _is_settlement(a)]
        account_label = "、".join((biz or accs)[:3])
        base_parts = [f"金额 {_fmt_money(amount)}", f"摘要「{summ or '(空)'}」"]
        if cp:
            base_parts.append(f"对手方「{cp}」")
        clues.append(_clue(
            type="DUPLICATE_TRANSACTION",
            level="高" if same_period else "低",
            title=("同期内重复交易" if same_period else "跨期同额同摘要交易"),
            account=account_label,
            period="、".join(periods[:3]),
            voucher_no="、".join(sorted(vnos)[:5]),
            amount=round(amount, 2),
            peer_group="金额+摘要+对手方 相同的分录",
            peer_n=len(rows),
            baseline="、".join(base_parts),
            deviation=f"出现在 {len(vnos)} 张凭证、{len(periods)} 个期间",
            method="金额+摘要+对手方 精确匹配，跨凭证计数",
            reason=(
                "同一金额与摘要分属不同凭证，可能是同期重复入账/重复付款，"
                "也可能是跨月的周期性业务（如固定租金），需按业务性质区分。"
            ),
            next_step=(
                "比对相关凭证的原始附件与银行流水，确认是否存在重复付款；"
                "若为周期性业务，核对合同约定频次是否一致。"
            ),
            evidence="凭证 " + "、".join(sorted(vnos)[:5]),
            _score=len(vnos) * (2.0 if same_period else 1.0),
        ))
    coverage["duplicate_buckets_checked"] = checked
    return clues


def _similar_transactions(records: list[dict], coverage: dict) -> list[dict]:
    """
    疑似重复交易（摘要近似版）。

    精确重复抓的是「金额+摘要+对手方逐字相同」，但实际重复入账常被改一两个字
    （「采购入库」与「采购入库-补录」）。这里在同一金额桶内两两比摘要相似度，
    落在 [0.75, 1.0) 的报为疑似，等级低于精确重复。

    O(n²) 只在单个金额桶内做，桶过大（>SIM_MAX_BUCKET）直接跳过并记录，
    避免大批量小额同额交易把耗时拖垮。
    """
    from difflib import SequenceMatcher

    buckets: dict[float, list[dict]] = defaultdict(list)
    for r in records:
        a = _amt(r)
        summ = _norm_text(r.get("summary"))
        if a < DUP_MIN_AMOUNT or not summ:
            continue
        buckets[round(a, 2)].append(r)

    clues: list[dict] = []
    checked = 0
    skipped = 0
    for amount, rows in buckets.items():
        if len(rows) < 2:
            continue
        if len(rows) > SIM_MAX_BUCKET:
            skipped += 1
            continue
        checked += 1
        seen_pairs: set[tuple[int, int]] = set()
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                ri, rj = rows[i], rows[j]
                vi, vj = str(ri.get("voucher_no") or ""), str(rj.get("voucher_no") or "")
                if not vi or vi == vj:       # 同一张凭证内的分录不构成重复入账
                    continue
                si = _norm_text(ri.get("summary"))
                sj = _norm_text(rj.get("summary"))
                if not si or not sj or si == sj:
                    continue              # 完全相同的情况由精确检测负责
                sim = SequenceMatcher(None, si, sj).ratio()
                if sim < SIM_SIMILAR_MIN:
                    continue
                key = (min(i, j), max(i, j))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                pi, pj = _period_key(ri), _period_key(rj)
                same_period = pi == pj
                clues.append(_clue(
                    type="SIMILAR_TRANSACTION",
                    level="中" if same_period else "低",
                    title="同额近似摘要交易",
                    account="、".join(sorted({str(ri.get("account") or ""),
                                              str(rj.get("account") or "")} - {""})[:3]),
                    period=f"{pi}、{pj}" if not same_period else pi,
                    voucher_no=f"{vi}、{vj}",
                    amount=round(amount, 2),
                    peer_group="同一金额桶内、摘要相似但不相同的分录",
                    peer_n=len(rows),
                    baseline=f"金额 {_fmt_money(amount)}；摘要「{si}」/「{sj}」",
                    deviation=f"摘要相似度 {sim:.2f}",
                    method="同额分桶 + 摘要序列相似度（0.75 ≤ 相似度 < 1）",
                    reason=(
                        "两笔金额一致、摘要高度相似但并非完全相同，可能是同一业务重复入账，"
                        "也可能是描述方式不同的两笔独立业务（如分次采购），需看原始附件。"
                    ),
                    next_step=(
                        "调阅两张凭证的原始单据与付款记录，核对是否为同一笔业务；"
                        "若为分次业务，确认合同或验收单能否支持两次独立发生。"
                    ),
                    evidence=f"凭证 {vi}、{vj}",
                    _score=sim * (2.0 if same_period else 1.0),
                ))
    coverage["similar_buckets_checked"] = checked
    coverage["similar_buckets_skipped"] = skipped
    return clues


# --------------------------------------------------------------------------
# D. 结构变化
# --------------------------------------------------------------------------
def _structure_shift(records: list[dict], coverage: dict) -> tuple[list[dict], list[dict]]:
    by_period: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    period_counts: dict[str, int] = defaultdict(int)
    for r in records:
        a = _amt(r)
        if a <= 0 or not r.get("account"):
            continue
        if _is_settlement(r.get("account")):
            continue  # 结算类科目是业务的结果，占比变化只是业务科目的镜像
        p = _period_key(r)
        by_period[p][str(r["account"]).strip()] += a
        period_counts[p] += 1

    # 记录数过少的期间不参与结构比较（单期只有几条记录时，占比变化没有意义）
    periods = sorted(p for p in by_period if period_counts[p] >= MIN_PERIOD_RECORDS)
    dropped = [p for p in by_period if period_counts[p] < MIN_PERIOD_RECORDS]
    if dropped:
        coverage["structure_periods_dropped"] = dropped[:12]
    if len(periods) < 2:
        coverage["structure_mode"] = (
            f"未执行：满足样本量（≥{MIN_PERIOD_RECORDS} 条/期）的期间不足 2 个"
        )
        return [], []

    totals = {p: sum(by_period[p].values()) for p in periods}
    all_accounts = sorted({a for p in periods for a in by_period[p]})
    # 「费用结构」只看费用类科目；若账簿里识别不到费用类科目，则退化为全部业务科目
    cost_accounts = [a for a in all_accounts if ("费" in a or "成本" in a or "支出" in a)]
    accounts = cost_accounts if cost_accounts else all_accounts
    coverage["structure_scope"] = "费用类科目" if cost_accounts else "全部业务科目（未识别到费用类科目）"

    # 结构表：科目 × 期间 金额
    structure = []
    for acc in accounts:
        row = {"account": acc}
        for p in periods:
            row[p] = round(by_period[p].get(acc, 0.0), 2)
        structure.append(row)

    clues: list[dict] = []
    for i in range(1, len(periods)):
        p0, p1 = periods[i - 1], periods[i]
        if totals.get(p0, 0) <= 0 or totals.get(p1, 0) <= 0:
            continue
        for acc in accounts:
            a0 = by_period[p0].get(acc, 0.0)
            a1 = by_period[p1].get(acc, 0.0)
            if a0 <= 0 and a1 <= 0:
                continue
            r0, r1 = a0 / totals[p0] * 100, a1 / totals[p1] * 100
            dpp = r1 - r0
            if abs(dpp) < STRUCT_SHIFT_PP:
                continue
            if max(a0, a1) < 0.01 * max(totals[p0], totals[p1]):
                continue  # 占比变化大但绝对额过小，不进线索
            ratio = (max(a0, a1) / min(a0, a1)) if min(a0, a1) > 0 else float("inf")
            if ratio < 1.8 and abs(dpp) < STRUCT_SHIFT_PP * 2:
                continue  # 金额没有实质变化，多为小基数上的占比抖动
            clues.append(_clue(
                type="STRUCTURE_SHIFT",
                level="中" if abs(dpp) >= STRUCT_SHIFT_PP * 2 else "低",
                title=f"{acc} 占费用总额比例{'上升' if dpp > 0 else '下降'} {abs(dpp):.1f} 个百分点",
                account=acc,
                period=p1,
                amount=round(a1, 2),
                peer_group="各期间费用结构",
                peer_n=len(periods),
                baseline=f"{p0} 占比 {r0:.1f}%（{_fmt_money(a0)}）→ {p1} 占比 {r1:.1f}%（{_fmt_money(a1)}）",
                deviation=f"结构占比 {dpp:+.1f} 个百分点",
                method="科目占期间总额比例变动",
                reason="结构占比突变通常意味着成本构成变化、科目重分类或某类费用集中发生。",
                next_step="与该期间业务变化核对：是否有新增业务线、科目重分类或一次性支出。",
                evidence=f"{acc}，{p0} → {p1}",
                _score=abs(dpp),
            ))
    coverage["structure_mode"] = f"已执行（{len(periods)} 个期间）"
    return clues, structure


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
LEVEL_ORDER = {"高": 0, "中": 1, "低": 2}
# 风险评分的权重：只做「线索轻重」的量化，不代表违规概率，也不对外称风险等级
LEVEL_WEIGHT = {"高": 10, "中": 4, "低": 1}
TYPE_ORDER = {
    "DUPLICATE_TRANSACTION": 0,
    "SIMILAR_TRANSACTION": 1,
    "AMOUNT_OUTLIER": 2,
    "PERIOD_END_CLUSTER": 3,
    "STRUCTURE_SHIFT": 4,
    "MONTH_SWING": 5,
    "WEEKEND_POSTING": 6,
}


def _dimensions(records: list[dict], structure: list[dict]) -> dict:
    """
    科目 / 月份 / 部门 三个维度的汇总，供前端画图与人工快速定位。

    只做汇总与占比，不做判断：占比高不等于有问题，异常线索另有专用分析。
    """
    total_amt = sum(_amt(r) for r in records) or 1.0

    # 科目维度
    acc: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for r in records:
        a = r.get("account")
        if not a:
            continue
        amt = _amt(r)
        acc[a]["amount"] += amt
        acc[a]["count"] += 1
    by_account = sorted(
        [{"account": k, "amount": round(v["amount"], 2), "count": v["count"],
          "share_pct": round(v["amount"] / total_amt * 100, 1)}
         for k, v in acc.items()],
        key=lambda x: -x["amount"])

    # 月份维度（含环比）
    mon: dict[str, dict] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0, "count": 0})
    for r in records:
        p = _period_key(r)
        mon[p]["debit"] += float(r.get("debit") or 0.0)
        mon[p]["credit"] += float(r.get("credit") or 0.0)
        mon[p]["count"] += 1
    by_period: list[dict] = []
    prev = None
    for p in sorted(mon):
        v = mon[p]
        mom = None
        if prev is not None and prev > 0:
            mom = round((v["debit"] - prev) / prev * 100, 1)
        by_period.append({
            "period": p, "debit": round(v["debit"], 2),
            "credit": round(v["credit"], 2), "count": v["count"],
            "mom_pct": mom,
        })
        prev = v["debit"]

    # 部门维度（原表无该列时返回空，并说明原因）
    dep: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for r in records:
        d = r.get("department")
        if not d:
            continue
        dep[d]["amount"] += _amt(r)
        dep[d]["count"] += 1
    by_department = sorted(
        [{"department": k, "amount": round(v["amount"], 2), "count": v["count"],
          "share_pct": round(v["amount"] / total_amt * 100, 1)}
         for k, v in dep.items()],
        key=lambda x: -x["amount"])

    return {
        "by_account": by_account,
        "by_period": by_period,
        "by_department": by_department,
        "has_department": bool(by_department),
        "n_accounts": len(by_account),
        "structure": structure,
    }


def _risk_score(by_level: dict, n_records: int) -> dict:
    """
    可解释的风险评分。

    公式：raw = 高×10 + 中×4 + 低×1
          密度 = raw / 记录数 × 100
          分数 = min(100, 密度 × 2)

    用途只是把「线索多不多、重不重」折算成一个可比的数，便于不同账套之间粗比；
    它不代表违规概率，也不构成审计结论。公式与权重随结果一起返回，便于复核。
    """
    raw = sum(LEVEL_WEIGHT.get(k, 0) * v for k, v in by_level.items())
    density = round(raw / n_records * 100, 2) if n_records else 0.0
    score = round(min(100.0, density * 2), 1)
    return {
        "score": score,
        "raw_weight": raw,
        "density": density,
        "formula": "raw=高×10+中×4+低×1; 密度=raw/记录数×100; 分数=min(100, 密度×2)",
        "level_weight": dict(LEVEL_WEIGHT),
        "disclaimer": "分数仅用于不同账套之间粗略比较，不代表违规概率或审计结论",
    }


def analyze_ledger_risk(records: list[dict], vouchers: list[dict] | None = None,
                        has_date_dim: bool | None = None) -> dict:
    """
    序时账风险分析主入口。

    返回：
      clues      : 风险线索列表（已排序、已截断）
      n_clues    : 线索总数
      by_level   : {'高':n,'中':n,'低':n}
      by_type    : {类型: 数量}
      coverage   : 各类分析是否实际执行（含降级说明）
      structure  : 科目 × 期间 金额表
      monthly    : 期间汇总表
    """
    if has_date_dim is None:
        has_date_dim = any(r.get("doc_date") for r in records)

    coverage: dict[str, Any] = {
        "n_records": len(records),
        "has_date_dim": bool(has_date_dim),
        "min_peer_n": MIN_PEER_N,
    }

    c_amount = _amount_outliers(records, coverage)
    c_time = _temporal_anomalies(records, bool(has_date_dim), coverage)
    c_dup = _duplicate_transactions(records, coverage)
    c_sim = _similar_transactions(records, coverage)
    c_struct, structure = _structure_shift(records, coverage)

    all_clues: list[dict] = c_amount + c_time + c_dup + c_sim + c_struct

    # 单类型截断：保留偏离强度最高的若干条
    by_type_raw: dict[str, list[dict]] = defaultdict(list)
    for c in all_clues:
        by_type_raw[c["type"]].append(c)
    trimmed: list[dict] = []
    for t, lst in by_type_raw.items():
        lst.sort(key=lambda x: x.get("_score", 0), reverse=True)
        trimmed.extend(lst[:MAX_CLUES_PER_TYPE])

    trimmed.sort(key=lambda x: (LEVEL_ORDER.get(x["level"], 9), TYPE_ORDER.get(x["type"], 9),
                                -float(x.get("_score", 0) or 0)))
    for c in trimmed:
        c.pop("_score", None)

    by_level = Counter(c["level"] for c in trimmed)
    by_type = Counter(c["type"] for c in trimmed)

    # 期间汇总（供前端画趋势）
    monthly_map: dict[str, dict[str, float]] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0, "count": 0})
    for r in records:
        p = _period_key(r)
        monthly_map[p]["debit"] += float(r.get("debit") or 0.0)
        monthly_map[p]["credit"] += float(r.get("credit") or 0.0)
        monthly_map[p]["count"] += 1
    monthly = [
        {"period": p, "debit": round(v["debit"], 2), "credit": round(v["credit"], 2), "count": v["count"]}
        for p, v in sorted(monthly_map.items())
    ]

    by_level_d = {k: by_level.get(k, 0) for k in ("高", "中", "低")}
    dimensions = _dimensions(records, structure)

    return {
        "clues": trimmed,
        "n_clues": len(trimmed),
        "n_clues_before_trim": len(all_clues),
        "by_level": by_level_d,
        "by_type": dict(by_type),
        "coverage": coverage,
        "structure": structure,
        "monthly": monthly,
        "dimensions": dimensions,
        "risk_score": _risk_score(by_level_d, len(records)),
    }


if __name__ == "__main__":
    import sys
    from tools.excel_analyzer import analyze_excel

    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample_ledger.xlsx"
    res = analyze_excel(path)
    risk = analyze_ledger_risk(res["records"], res["vouchers"])
    print(f"记录 {res['n_rows']} 条 | 线索 {risk['n_clues']} 条 {risk['by_level']}")
    print("覆盖情况:", risk["coverage"])
    for c in risk["clues"][:5]:
        print(f"\n[{c['level']}] {c['type']} · {c['title']}")
        print("  基线:", c["baseline"])
        print("  偏离:", c["deviation"], "|", c["method"])
        print("  建议:", c["next_step"])
