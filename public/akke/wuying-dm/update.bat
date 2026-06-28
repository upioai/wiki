@echo off
chcp 65001 >nul
REM ============================================================================
REM Akke 云电脑 agent 一键更新（不用装 git，从 GitHub 直接下载覆盖）
REM
REM 用法：双击本文件即可
REM
REM 工作流程：
REM   1. 备份现有 py 文件 → .bak（出错可回滚）
REM   2. 从 GitHub raw 拉最新版的 3 个 py 文件覆盖
REM   3. 提示运营手动关掉旧 agent 黑窗口 → 双击 start.bat 重启
REM
REM 不自动杀 py.exe 也不自动重启 agent —— 怕误杀同事别的 py 程序，
REM 也怕新 agent 没起来运营不知道。明确两步：本脚本只下文件，
REM 运营自己点 start.bat 起 agent。
REM ============================================================================

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Akke 云电脑 agent 更新工具
echo   目录: %CD%
echo ============================================================
echo.

REM ── [1/3] 备份 ─────────────────────────────────────────────
echo [1/3] 备份现有文件 → *.bak ...
if exist wuying_poll_agent.py        copy /Y wuying_poll_agent.py        wuying_poll_agent.py.bak        >nul
if exist douyin_dm_grounded.py       copy /Y douyin_dm_grounded.py       douyin_dm_grounded.py.bak       >nul
if exist douyin_rc_reply_grounded.py copy /Y douyin_rc_reply_grounded.py douyin_rc_reply_grounded.py.bak >nul
if exist douyin_comment_grounded.py  copy /Y douyin_comment_grounded.py  douyin_comment_grounded.py.bak  >nul
if exist douyin_inbox_uia.py         copy /Y douyin_inbox_uia.py         douyin_inbox_uia.py.bak         >nul
if exist douyin_dm_autoreply.py      copy /Y douyin_dm_autoreply.py      douyin_dm_autoreply.py.bak      >nul
if exist douyin_dm_web_capture.py    copy /Y douyin_dm_web_capture.py    douyin_dm_web_capture.py.bak    >nul
if exist douyin_dm_web_reply.py      copy /Y douyin_dm_web_reply.py      douyin_dm_web_reply.py.bak      >nul
if exist douyin_dm_web_grounded.py   copy /Y douyin_dm_web_grounded.py   douyin_dm_web_grounded.py.bak   >nul
echo     完成（如需回滚：把 .bak 改回原文件名即可）
echo.

REM ── [2/3] 下载最新版 ──────────────────────────────────────
echo [2/3] 从 GitHub 下载最新版 ...

REM Akke 仓私有, raw 无 token 必 404 (2026-06-24 实测); 改拉公开镜像 upioai/wiki (token-free),
REM 由 .github/workflows/sync-wuying-scripts.yml 在合 main 时自动镜像最新版.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { ^
  $ErrorActionPreference = 'Stop'; ^
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
  $base = 'https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm'; ^
  $files = @('wuying_poll_agent.py', 'douyin_dm_grounded.py', 'douyin_rc_reply_grounded.py', 'douyin_comment_grounded.py', 'douyin_inbox_uia.py', 'douyin_dm_autoreply.py', 'douyin_dm_web_capture.py', 'douyin_dm_web_reply.py', 'douyin_dm_web_grounded.py'); ^
  foreach ($f in $files) { ^
    try { ^
      Invoke-WebRequest -Uri \"$base/$f\" -OutFile \".\$f\" -UseBasicParsing -TimeoutSec 30; ^
      Write-Host \"     OK   $f\"; ^
    } catch { ^
      Write-Host \"     FAIL $f : $($_.Exception.Message)\" -ForegroundColor Red; ^
      exit 1; ^
    } ^
  } ^
}"

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   下载失败！可能原因和处置：
    echo ============================================================
    echo   1. 网络一时抽风 → 关掉这个窗口，等 30 秒，重新双击 update.bat
    echo   2. 反复失败       → 找 PM 协助，可能是 GitHub 被卡了
    echo   3. 回滚            → 把 *.py.bak 改回 *.py
    echo.
    pause
    exit /b 1
)

echo     全部下载完成
echo.

REM ── [3/3] 提示重启 ─────────────────────────────────────────
echo ============================================================
echo   [3/3] 文件已更新！现在请手动重启 agent
echo ============================================================
echo.
echo   第 1 步: 找到那个跑着 agent 的【黑窗口】
echo            （标题应该有 wuying_poll_agent 或类似字样）
echo.
echo   第 2 步: 直接点右上角【×】关掉它
echo.
echo   第 3 步: 重新启动 agent（本机【没有 start.bat】，二选一）：
echo            (推荐) 直接【重启这台云电脑】—— 开机自启会自动重跑 agent + 读最新 .env
echo            (或)   开 PowerShell，cd 到本目录，跑:  py wuying_poll_agent.py
echo.
echo   第 4 步: 看到黑窗口里有 "every 60s claim=..." 这类滚动日志
echo            = ✅ 已经在跑新版
echo.
echo ============================================================
echo   完成后请到 Lark【云电脑监控群】回复 ✅ + 你的名字
echo ============================================================
echo.

pause
endlocal
