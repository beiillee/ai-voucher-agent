# -*- coding: utf-8 -*-
"""
评估入口
========
用法：
    python3 -m evaluation.run              # 跑评估并写 output/evaluation/
    python3 -m evaluation.run --json       # 只打印 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate import run_evaluation, to_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="在演示集上跑评估")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--repeats", type=int, default=3, help="耗时测量的重复次数")
    ap.add_argument("--out", default="output/evaluation", help="输出目录")
    args = ap.parse_args()

    result = run_evaluation(repeats=args.repeats)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(result)
    (out / "evaluation.md").write_text(md, encoding="utf-8")

    f, m, a, p = result["fields"], result["matching"], result["anomalies"], result["performance"]
    print("=" * 52)
    print(f"字段抽取准确率   : {f['accuracy'] * 100:.1f}%  ({f['correct']}/{f['total']})")
    print(f"匹配  P / R / F1 : {m['precision'] * 100:.1f}% / {m['recall'] * 100:.1f}% / {m['f1'] * 100:.1f}%")
    print(f"异常  P / R / F1 : {a['precision'] * 100:.1f}% / {a['recall'] * 100:.1f}% / {a['f1'] * 100:.1f}%")
    print(f"凭证级误报率     : {a['false_positive_rate'] * 100:.1f}%")
    print(f"端到端耗时中位数 : {p['e2e_seconds_median']} s  峰值内存 {p['peak_memory_mb']} MB")
    print(f"批量失败率       : {p['batch_failure_rate'] * 100:.1f}%")
    print("=" * 52)
    print(f"报告：{out / 'evaluation.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
