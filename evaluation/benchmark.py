# -*- coding: utf-8 -*-
"""
性能基准
========
按两个维度测吞吐，数字全部来自本机实测：

  1. 文件数：同一份 PDF 复制 N 份，测单文件处理开销是否线性
  2. 账簿规模：16 行小账 vs 1070 行演示账，测账侧规模效应

用法：
    python3 -m evaluation.benchmark
    python3 -m evaluation.benchmark --files 8
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.batch import run_batch  # noqa: E402

SMALL_LEDGER = ROOT / "data" / "demo_recon" / "ledger.xlsx"   # 16 行
LARGE_LEDGER = ROOT / "data" / "demo_ledger.xlsx"             # 1070 行
SAMPLE_PDF = ROOT / "data" / "demo_recon" / "vouchers.pdf"    # 7 张凭证


def _time_run(pdf_paths: list[Path], ledger: Path, out: Path) -> dict:
    tracemalloc.start()
    t0 = time.perf_counter()
    res = run_batch(pdf_paths, ledger, output_dir=out)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "seconds": round(elapsed, 3),
        "peak_mb": round(peak / 1024 / 1024, 1),
        "pdf_vouchers": res["summary"]["total_pdf"],
        "ledger_vouchers": res["summary"]["total_ledger"],
        "failed": res["summary"]["n_files_failed"],
    }


def run_benchmark(file_counts=(1, 2, 4, 8), repeats: int = 2) -> dict:
    results = {"file_scaling": [], "ledger_scaling": []}

    # 1) 文件数扩展：账簿固定为 16 行小账
    for n in file_counts:
        tmp = Path(tempfile.mkdtemp())
        try:
            pdfs = []
            for i in range(n):
                p = tmp / f"copy_{i+1}.pdf"
                shutil.copy(SAMPLE_PDF, p)
                pdfs.append(p)
            runs = [_time_run(pdfs, SMALL_LEDGER, tmp / f"out{n}") for _ in range(repeats)]
            results["file_scaling"].append({
                "n_files": n,
                "seconds_median": round(statistics.median(r["seconds"] for r in runs), 3),
                "seconds_per_file": round(
                    statistics.median(r["seconds"] for r in runs) / n, 3),
                "peak_mb": max(r["peak_mb"] for r in runs),
                "pdf_vouchers": runs[0]["pdf_vouchers"],
                "failed": runs[0]["failed"],
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 2) 账簿规模：固定一份 PDF，换小账与大账
    for label, ledger in (("16 行（演示小账）", SMALL_LEDGER),
                          ("1070 行（演示大账）", LARGE_LEDGER)):
        tmp = Path(tempfile.mkdtemp())
        try:
            runs = [_time_run([SAMPLE_PDF], ledger, tmp / f"out{i}") for i in range(repeats)]
            med = statistics.median(r["seconds"] for r in runs)
            results["ledger_scaling"].append({
                "ledger": label,
                "n_ledger_vouchers": runs[0]["ledger_vouchers"],
                "seconds_median": round(med, 3),
                "per_1000_vouchers": (round(med / max(runs[0]["ledger_vouchers"], 1) * 1000, 3)
                                      if runs[0]["ledger_vouchers"] else None),
                "peak_mb": max(r["peak_mb"] for r in runs),
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    results["note"] = ("演示 PDF 含文字层，不触发 OCR；扫描件耗时按页数线性增加，"
                       "本表不含 OCR 时间。沙箱环境无 GPU。")
    return results


def to_markdown(r: dict) -> str:
    lines = [
        "# 性能基准",
        "",
        "## 一、文件数扩展（账簿固定 16 行）",
        "",
        "| 文件数 | 总耗时(s) | 单文件(s) | 峰值内存(MB) | PDF凭证 | 失败 |",
        "|---|---|---|---|---|---|",
    ]
    for x in r["file_scaling"]:
        lines.append(f"| {x['n_files']} | {x['seconds_median']} | {x['seconds_per_file']} | "
                     f"{x['peak_mb']} | {x['pdf_vouchers']} | {x['failed']} |")
    lines += [
        "",
        "## 二、账簿规模（固定 1 份 PDF）",
        "",
        "| 账簿 | 账内凭证数 | 耗时(s) | 每千张凭证(s) | 峰值内存(MB) |",
        "|---|---|---|---|---|",
    ]
    for x in r["ledger_scaling"]:
        lines.append(f"| {x['ledger']} | {x['n_ledger_vouchers']} | {x['seconds_median']} | "
                     f"{x['per_1000_vouchers']} | {x['peak_mb']} |")
    lines += ["", f"> {r['note']}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default="1,2,4,8", help="文件数序列，逗号分隔")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default="output/evaluation")
    args = ap.parse_args()

    counts = tuple(int(x) for x in str(args.files).split(",") if x.strip())
    res = run_benchmark(counts, repeats=args.repeats)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    md = to_markdown(res)
    (out / "benchmark.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"已写入 {out / 'benchmark.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
