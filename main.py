# -*- coding: utf-8 -*-
"""
命令行入口（供直接运行 / PyInstaller 打包成 .exe）。

用法 1（参数）：  voucher-agent 凭证.pdf 序时账.xlsx [输出目录] [--use-llm]
用法 2（双击）：  不带参数运行，按提示输入文件路径，适合打包成 exe 后给非技术用户使用。
                 双击模式下会询问「是否启用 LLM 异常解释」，启用则提示填入你自己的 API Key。

说明：LLM 为可选增强。未配置 API Key 时，核对逻辑（OCR/匹配/异常/报告）照常完整运行。
      Key 不会被写进程序，每次运行按需填写，使用你自己的额度。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.orchestrator import run

load_dotenv()  # 若同目录存在 .env，则自动加载其中环境变量


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


def _setup_llm_interactive() -> bool:
    """双击模式下询问是否启用 LLM，并收集 Key。返回是否启用。"""
    ans = _ask("是否启用 LLM 异常解释（需 API Key，y/N）: ", "n").lower()
    if ans not in ("y", "yes"):
        return False

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        key = _ask("请输入你的 OPENAI_API_KEY（DeepSeek/OpenAI 等，留空则跳过）: ", "")
        if not key:
            print("[提示] 未提供 Key，LLM 已跳过，仅输出规则核对结果。")
            return False
        os.environ["OPENAI_API_KEY"] = key
        # 兼容 OpenAI 协议：可填 DeepSeek / 通义 / 本地等任意兼容服务
        base = _ask("API 地址(默认 https://api.deepseek.com/v1): ",
                    "https://api.deepseek.com/v1")
        os.environ["OPENAI_BASE_URL"] = base
        model = _ask("模型名(默认 deepseek-chat): ", "deepseek-chat")
        os.environ["LLM_MODEL"] = model
    return True


def main() -> None:
    use_llm = False

    if len(sys.argv) >= 3:
        pdf = sys.argv[1]
        led = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "output"
        # 命令行模式：显式 --use-llm 且环境中有 Key 才启用
        if "--use-llm" in sys.argv and os.getenv("OPENAI_API_KEY"):
            use_llm = True
    else:
        print("===== AI 财务凭证智能核对 Agent（命令行）=====")
        pdf = _ask("请输入凭证 PDF 路径: ", "")
        led = _ask("请输入序时账 Excel 路径: ", "")
        out = _ask("输出目录(回车默认 output): ", "output")
        use_llm = _setup_llm_interactive()

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

    res = run(pdf, led, output_dir=out, use_llm=use_llm)
    s = res["reconciliation"]["summary"]
    print("\n===== 核对完成 =====")
    print(f"PDF 凭证: {s['total_pdf']} | 序时账凭证: {s['total_ledger']}")
    print(f"匹配成功: {s['matched']} | 金额不一致: {s['amount_mismatch']} | 仅 PDF 有: {s['pdf_only']}")
    print(f"匹配率: {s['match_rate_pct']}% | 异常: {res['anomalies']['n_anomalies']}")
    if res.get("llm_used"):
        print("🤖 LLM 异常解释：已生成（见报告「异常清单」对应说明）")
    print(f"报告已生成: {res['report_path']}")
    _pause()


if __name__ == "__main__":
    main()
