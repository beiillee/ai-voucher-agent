# -*- coding: utf-8 -*-
"""
评估指标
========
只放纯函数，输入是「预测」与「标准答案」，输出是可复算的指标。
所有指标都由真实运行结果计算，不接受任何写死的数字。

口径约定：
  TP  预测存在、标准答案也存在，且指向同一对象
  FP  预测存在，但标准答案里没有（或指向了别的对象）
  FN  标准答案里有，预测没找到
  TN  标准答案里没有，预测也没报（仅在同构负样本上才有意义）
"""
from __future__ import annotations


def prf(tp: int, fp: int, fn: int) -> dict:
    """由 TP/FP/FN 算精确率、召回率、F1。分母为 0 时置 0 而不是抛异常。"""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def false_positive_rate(fp: int, tn: int) -> float:
    """误报率 = FP / (FP + TN)。没有负样本时返回 0 并需在报告中说明。"""
    denom = fp + tn
    return round(fp / denom, 4) if denom else 0.0


def field_accuracy(checks: list[dict]) -> dict:
    """
    字段抽取准确率。

    checks 每项：{field, expected, actual, ok}
    返回总体准确率 + 按字段分组的准确率，避免用一个总数掩盖某个字段全错。
    """
    total = len(checks)
    ok = sum(1 for c in checks if c["ok"])
    by_field: dict[str, dict] = {}
    for c in checks:
        f = c["field"]
        d = by_field.setdefault(f, {"n": 0, "ok": 0})
        d["n"] += 1
        d["ok"] += 1 if c["ok"] else 0
    for f, d in by_field.items():
        d["accuracy"] = round(d["ok"] / d["n"], 4) if d["n"] else 0.0
    return {
        "total": total,
        "correct": ok,
        "accuracy": round(ok / total, 4) if total else 0.0,
        "by_field": by_field,
    }


def amount_equal(a, b, tol: float = 0.01) -> bool:
    """金额比较：分位容差内视为相等，None 一律判否。"""
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False
