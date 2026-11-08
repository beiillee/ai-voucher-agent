# -*- coding: utf-8 -*-
"""
评估执行器
==========
在带人工标准答案的演示集上，真实跑一遍系统并计算指标：

  1. 字段抽取准确率（凭证号 / 日期 / 金额 / 科目）
  2. 账证匹配 Precision / Recall / F1（含「找错对象」与「漏找」分开计）
  3. 异常检测 Precision / Recall / F1 与凭证级误报率
  4. 批量处理耗时（多次取中位数）与失败率

所有数字都来自当前代码的实际运行结果。标准答案写在
data/demo_recon/labels.json 里，与生成脚本同源，不是从运行结果反填的。
"""
from __future__ import annotations

import json
import statistics
import time
import tracemalloc
from pathlib import Path
from typing import Any

from agent.orchestrator import run
from agent.batch import run_batch
from evaluation.metrics import prf, field_accuracy, amount_equal, false_positive_rate

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "data" / "demo_recon"
DEMO_PDF = DEMO_DIR / "vouchers.pdf"
DEMO_XLSX = DEMO_DIR / "ledger.xlsx"
DEMO_LABELS = DEMO_DIR / "labels.json"
SAMPLE_PDF = ROOT / "data" / "sample_voucher.pdf"
SAMPLE_XLSX = ROOT / "data" / "sample_ledger.xlsx"


# ---------------------------------------------------------------- 1. 字段


def evaluate_fields(res: dict, labels: dict) -> dict:
    """逐字段比对抽取结果与标准答案。"""
    by_no = {v.get("voucher_no"): v for v in res["pdf_vouchers"]}
    checks: list[dict] = []
    for vno, exp in labels["expected_fields"].items():
        v = by_no.get(vno) or {}
        checks.append({"voucher_no": vno, "field": "voucher_no",
                       "expected": exp["voucher_no"], "actual": v.get("voucher_no"),
                       "ok": v.get("voucher_no") == exp["voucher_no"]})
        checks.append({"voucher_no": vno, "field": "date",
                       "expected": exp["date"], "actual": v.get("date"),
                       "ok": v.get("date") == exp["date"]})
        checks.append({"voucher_no": vno, "field": "debit_total",
                       "expected": exp["debit_total"], "actual": v.get("debit_total"),
                       "ok": amount_equal(v.get("debit_total"), exp["debit_total"])})
        accs = v.get("accounts") or []
        checks.append({"voucher_no": vno, "field": "account",
                       "expected": exp["account"],
                       "actual": "、".join(accs),
                       "ok": exp["account"] in accs})
    out = field_accuracy(checks)
    out["details"] = checks
    return out


# ---------------------------------------------------------------- 2. 匹配


def evaluate_matching(res: dict, labels: dict) -> dict:
    """
    账证对应关系评估。

    对每张 PDF 凭证判断「有没有落到正确的那张账内凭证」：
      - 找对 -> TP
      - 找错对象 -> 既算 FP（报了错的）也算 FN（正确的没找到）
      - 该找没找到 -> FN
      - 本就不该有对应（PDF_ONLY 场景）却报了个对象 -> FP
    状态判定准确率单独统计（找对对象后，金额/日期结论是否也对）。
    """
    pairs = {p["pdf_voucher"].get("voucher_no"): p
             for p in res["reconciliation"]["pairs"]}
    tp = fp = fn = tn = 0
    status_ok = 0
    details: list[dict] = []

    for vno, exp in labels["expected_pairs"].items():
        p = pairs.get(vno)
        exp_vno = exp["ledger_voucher_no"]
        exp_status = exp["status"]
        if p is None:
            fn += 1 if exp_vno else 0
            details.append({"voucher_no": vno, "expected": exp_vno, "actual": None,
                            "outcome": "未提取到该凭证", "status_expected": exp_status,
                            "status_actual": None})
            continue
        actual_vno = (p.get("ledger_voucher") or {}).get("voucher_no")
        status = p.get("match_status")

        if exp_vno is None:
            if actual_vno is None:
                tn += 1
                outcome = "正确判定为无对应"
            else:
                fp += 1
                outcome = "误配到账内凭证"
        elif actual_vno == exp_vno:
            tp += 1
            outcome = "对应正确"
            if status == exp_status:
                status_ok += 1
        elif actual_vno is None:
            fn += 1
            outcome = "应找到但未找到"
        else:
            fp += 1
            fn += 1
            outcome = f"找错对象（应为 {exp_vno}）"

        details.append({"voucher_no": vno, "expected": exp_vno, "actual": actual_vno,
                        "outcome": outcome, "status_expected": exp_status,
                        "status_actual": status})

    m = prf(tp, fp, fn)
    m.update({
        "tn": tn,
        "n_cases": len(labels["expected_pairs"]),
        "status_accuracy": round(status_ok / tp, 4) if tp else 0.0,
        "details": details,
    })
    return m


# ---------------------------------------------------------------- 3. 异常


def evaluate_anomalies(res: dict, labels: dict) -> dict:
    """
    异常检测评估，按（凭证号, 异常类型）事件计。

    is_info 的条目（LEDGER_ONLY、OCR 噪声提示）不计入 TP/FP，
    它们是方法学说明，不是对某笔业务的判断。
    """
    pred = {(a.get("voucher_no"), a.get("type"))
            for a in res["anomalies"]["anomalies"] if not a.get("is_info")}
    truth = {(x["voucher_no"], x["type"]) for x in labels["expected_anomalies"]}

    tp = len(pred & truth)
    fp_set = pred - truth
    fn_set = truth - pred
    m = prf(tp, len(fp_set), len(fn_set))

    # 凭证级误报率：报了异常、但该凭证不在标准答案异常清单里的比例
    n_vouchers = len(labels["expected_pairs"])
    truth_vnos = {v for v, _ in truth}
    fp_vnos = {v for v, _ in fp_set if v and v not in truth_vnos}
    m.update({
        "n_vouchers": n_vouchers,
        "false_positive_rate": false_positive_rate(len(fp_vnos), n_vouchers - len(fp_vnos)),
        "fp_events": sorted(f"{v}:{t}" for v, t in fp_set),
        "fn_events": sorted(f"{v}:{t}" for v, t in fn_set),
        "predicted_events": sorted(f"{v}:{t}" for v, t in pred),
        "truth_events": sorted(f"{v}:{t}" for v, t in truth),
    })
    return m


# ---------------------------------------------------------------- 4. 性能


def evaluate_performance(repeats: int = 3) -> dict:
    """端到端耗时：多次运行取中位数，同时记录峰值内存。"""
    times: list[float] = []
    peak_mem = 0
    for _ in range(repeats):
        tracemalloc.start()
        t0 = time.perf_counter()
        run(DEMO_PDF, DEMO_XLSX, output_dir=str(ROOT / "output" / "_perf"))
        times.append(time.perf_counter() - t0)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mem = max(peak_mem, peak)
    files = [DEMO_PDF, SAMPLE_PDF]
    t0 = time.perf_counter()
    batch = run_batch(files, SAMPLE_XLSX, output_dir=str(ROOT / "output" / "_perf_batch"))
    batch_time = time.perf_counter() - t0
    s = batch["summary"]
    return {
        "e2e_seconds_median": round(statistics.median(times), 3),
        "e2e_seconds_min": round(min(times), 3),
        "e2e_seconds_max": round(max(times), 3),
        "peak_memory_mb": round(peak_mem / 1024 / 1024, 1),
        "repeats": repeats,
        "batch_files": s["n_files"],
        "batch_ok": s["n_files_ok"],
        "batch_failed": s["n_files_failed"],
        "batch_failure_rate": round(s["n_files_failed"] / s["n_files"], 4) if s["n_files"] else 0.0,
        "batch_seconds": round(batch_time, 3),
    }


# ---------------------------------------------------------------- 主入口


def run_evaluation(repeats: int = 3) -> dict[str, Any]:
    labels = json.loads(DEMO_LABELS.read_text(encoding="utf-8"))
    res = run(DEMO_PDF, DEMO_XLSX, output_dir=str(ROOT / "output" / "_eval"))

    fields = evaluate_fields(res, labels)
    matching = evaluate_matching(res, labels)
    anomalies = evaluate_anomalies(res, labels)
    perf = evaluate_performance(repeats=repeats)

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": {
            "name": "data/demo_recon",
            "n_pdf_vouchers": labels["n_pdf_vouchers"],
            "n_ledger_vouchers": labels["n_ledger_vouchers"],
            "labels_file": str(DEMO_LABELS.relative_to(ROOT)),
        },
        "fields": fields,
        "matching": matching,
        "anomalies": anomalies,
        "performance": perf,
        "system_summary": res["reconciliation"]["summary"],
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def to_markdown(r: dict) -> str:
    f, m, a, p = r["fields"], r["matching"], r["anomalies"], r["performance"]
    lines = [
        "# 评估报告",
        "",
        f"生成时间：{r['generated_at']}　｜　数据集：`{r['dataset']['name']}`"
        f"（{r['dataset']['n_pdf_vouchers']} 张 PDF 凭证 / {r['dataset']['n_ledger_vouchers']} 张账内凭证）",
        "",
        "> 本报告所有数字均由当前代码在演示集上实际运行得出，标准答案见 "
        "`data/demo_recon/labels.json`。演示集规模小，指标用于回归对比，不代表真实业务分布。",
        "",
        "## 一、字段抽取准确率",
        "",
        f"总体：**{_pct(f['accuracy'])}**（{f['correct']}/{f['total']} 个字段）",
        "",
        "| 字段 | 正确 | 总数 | 准确率 |",
        "|---|---|---|---|",
    ]
    for name, d in f["by_field"].items():
        lines.append(f"| {name} | {d['ok']} | {d['n']} | {_pct(d['accuracy'])} |")

    lines += [
        "",
        "## 二、账证匹配",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| Precision | {_pct(m['precision'])} |",
        f"| Recall | {_pct(m['recall'])} |",
        f"| F1 | {_pct(m['f1'])} |",
        f"| 状态判定准确率（找对对象后，金额/日期结论也对） | {_pct(m['status_accuracy'])} |",
        f"| TP / FP / FN / TN | {m['tp']} / {m['fp']} / {m['fn']} / {m['tn']} |",
        "",
        "## 三、异常检测",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| Precision | {_pct(a['precision'])} |",
        f"| Recall | {_pct(a['recall'])} |",
        f"| F1 | {_pct(a['f1'])} |",
        f"| 凭证级误报率 | {_pct(a['false_positive_rate'])} |",
        f"| TP / FP / FN | {a['tp']} / {a['fp']} / {a['fn']} |",
        "",
        f"标准答案事件：{'、'.join(a['truth_events']) or '无'}",
        "",
        f"实际报出事件：{'、'.join(a['predicted_events']) or '无'}",
        "",
        f"误报：{'、'.join(a['fp_events']) or '无'}　｜　漏报：{'、'.join(a['fn_events']) or '无'}",
        "",
        "## 四、性能与稳定性",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 端到端耗时（中位数 / {p['repeats']} 次） | {p['e2e_seconds_median']} s |",
        f"| 耗时区间 | {p['e2e_seconds_min']} – {p['e2e_seconds_max']} s |",
        f"| 峰值内存 | {p['peak_memory_mb']} MB |",
        f"| 批量文件数 / 成功 / 失败 | {p['batch_files']} / {p['batch_ok']} / {p['batch_failed']} |",
        f"| 批量失败率 | {_pct(p['batch_failure_rate'])} |",
        f"| 批量耗时 | {p['batch_seconds']} s |",
        "",
        "> 耗时在沙箱环境（无 GPU、演示 PDF 含文字层不触发 OCR）下测得；"
        "扫描件走 OCR 时耗时按页数线性增加，未计入本表。",
    ]
    return "\n".join(lines) + "\n"
