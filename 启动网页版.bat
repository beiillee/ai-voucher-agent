@echo off
REM ============================================================
REM  AI 财务凭证智能核对 Agent —— Windows 一键启动网页版
REM  双击本文件即可（前提：已安装 Python 3.11+ 并勾选 Add to PATH）
REM ============================================================
cd /d "%~dp0"

where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo [错误] 未检测到 Python。请先到 https://www.python.org 安装 Python 3.11+，
    echo        安装时务必勾选 "Add python.exe to PATH"。
    pause
    exit /b
)

echo 正在检查并安装依赖（若已安装会自动跳过）...
python -m pip install -r requirements.txt --quiet

echo.
echo 正在启动网页，浏览器将自动打开 http://localhost:8501 ...
echo （关闭本窗口即停止程序）
echo.
python -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
