@echo off
REM ============================================================================
REM Akke 云电脑发私信 — 启动 agent（双击即可）
REM 前提：抖音 PC 已登录并最大化；.env 已配好。
REM 这个黑窗口启动后【保持打开别关】，它会每 60s 自动检查并发信。
REM 停止：关掉这个窗口，或按 Ctrl+C。
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"
echo === 启动 Akke 发信 agent ===
echo 抖音 PC 请保持登录 + 最大化 + 在前台。
echo 这个窗口保持打开别关。停止请直接关窗口。
echo.

REM ---- 启动前自动拉最新 poll_agent（best-effort）----
REM 拉成功且体积正常才覆盖；拉不到 / 拉到半截截断文件都用本地现有版继续，不卡启动。
set "AGENT_URL=https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm/wuying_poll_agent.py"
echo [self-update] 拉取最新 poll_agent ...
curl.exe -fsS "%AGENT_URL%" -o wuying_poll_agent.py.new 2>nul
if exist wuying_poll_agent.py.new (
    for %%A in (wuying_poll_agent.py.new) do (
        if %%~zA GTR 5000 (
            copy /Y wuying_poll_agent.py.new wuying_poll_agent.py >nul
            echo [self-update] 已更新到最新版。
        ) else (
            echo [self-update] 下载文件异常偏小，忽略，用本地现有版本。
        )
    )
    del wuying_poll_agent.py.new >nul 2>nul
) else (
    echo [self-update] 拉取失败（GitHub 可能被墙），用本地现有版本继续。
)
echo.

py wuying_poll_agent.py
echo.
echo agent 已退出。按任意键关闭窗口。
pause >nul
