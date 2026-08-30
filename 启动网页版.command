#!/bin/bash
# ============================================================
#  AI 财务凭证智能核对 Agent —— macOS 一键启动网页版
#  在 Finder 中双击本文件即可（前提：已安装 Python 3.11+）
# ============================================================
cd "$(dirname "$0")" || exit 1

if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 Python。请先安装 Python 3.11+（推荐用 brew install python@3.11）"
    read -p "按回车退出..."
    exit 1
fi

echo "正在检查并安装依赖（若已安装会自动跳过）..."
python3 -m pip install -r requirements.txt --quiet

echo ""
echo "正在启动网页，浏览器将自动打开 http://localhost:8501 ..."
echo "（关闭本终端窗口即停止程序）"
echo ""
python3 -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
