# -*- coding: utf-8 -*-
"""
命令行入口（供直接运行 / PyInstaller 打包成 .exe）。

用法 1（参数）：  voucher-agent 凭证.pdf 序时账.xlsx [输出目录]
用法 2（双击）：  不带参数运行，按提示输入文件路径，适合打包成 exe 后给非技术用户使用。
"""
from __future__ import annotations

import sys
from pathlib import Path

from agent.orchestrator import run


def _ask(prompt: str, default: str) -> str:
    try:
        val = input(prompt).strip().strip('"').strip("'")
    except EOFError:
        val = ""
    return val or default


def _pause() -> None:
    try:
        input("\n按回车退出...")
    except EOFError:
        pass


def main() -> None:
    if len(sys.argv) >= 3:
        pdf = sys.argv[1]
        led = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "output"
    else:
        print("===== AI 财务凭证智能核对 Agent（命令行）=====")
        pdf = _ask("请输入凭证 PDF 路径: ", "")
        led = _ask("请输入序时账 Excel 路径: ", "")
        out = _ask("输出目录(回车默认 output): ", "output")

    if not pdf or not led:
        print("[错误] 未提供凭证 PDF 或序时账 Excel 路径。")
        _pause()
        return

    if not Path(pdf).exists():
        print(f"[错误] 找不到 PDF 文件: {pdf}")
        _pause()
        return
    if not Path(led).exists():
        print(f"[错误] 找不到 Excel 文件: {led}")
        _pause()
        return

    res = run(pdf, led, output_dir=out)
    s = res["reconciliation"]["summary"]
    print("\n===== 核对完成 =====")
    print(f"PDF 凭证: {s['total_pdf']} | 序时账凭证: {s['total_ledger']}")
    print(f"匹配成功: {s['matched']} | 金额不一致: {s['amount_mismatch']} | 仅 PDF 有: {s['pdf_only']}")
    print(f"匹配率: {s['match_rate_pct']}% | 异常: {res['anomalies']['n_anomalies']}")
    print(f"报告已生成: {res['report_path']}")
    _pause()


if __name__ == "__main__":
    main()
