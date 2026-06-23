@echo off
REM ============================================================================
REM Akke cloud-PC tuwen agent launcher (Wuying). Double-click to start.
REM
REM Prereq files in this dir (C:\akke-wuying\worker\scripts\wuying-tuwen\):
REM   wuying_tuwen_agent.py    creator_publisher.py
REM   ../wuying-dm/.env  (shared with DM channel; provides SUPABASE_* + AKKE_TUWEN_*)
REM
REM Before each boot: Edge logged into creator.douyin.com + window maximized;
REM   IME = English; SCREEN = 2560x1600 (so VL locators match).
REM
REM ASCII-only on purpose: cmd.exe garbles UTF-8 Chinese in .bat (.py reads UTF-8 fine).
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"

REM --- self-update: pull latest agent + publisher from public mirror (token-free) ---
REM .bat self-update needs manual redeploy (run this download once on each box):
REM   curl.exe -fsSO https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-tuwen/start-agent.bat
REM .py self-update happens on every double-click below.
echo === self-update: pull latest tuwen scripts from public mirror ===
set "MIRROR=https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-tuwen"
call :pull wuying_tuwen_agent.py
call :pull creator_publisher.py
echo === self-update done ===
echo.

echo === Akke cloud-PC tuwen agent starting ===
echo.
echo Prereq checklist:
echo   1. Edge open + logged into creator.douyin.com (any tab)
echo   2. ../wuying-dm/.env has AKKE_TUWEN_ENABLED=1 + AKKE_TUWEN_ASSIGNEE=^<your name^>
echo   3. Default poll = every 5min; set AKKE_TUWEN_POLL_INTERVAL=30 in .env to speed up
echo.
echo Keep this window open. Stop = close window or Ctrl+C.
echo.
py wuying_tuwen_agent.py
echo.
echo agent exited. Press any key to close window.
pause >nul
exit /b

:pull
curl.exe -fsS "%MIRROR%/%~1" -o "%~1.new"
if errorlevel 1 ( echo   [keep] %~1 ^(fetch failed^) & del "%~1.new" 2>nul & exit /b )
for %%S in ("%~1.new") do if %%~zS GTR 0 ( move /y "%~1.new" "%~1" >nul & echo   [ok]   %~1 ) else ( echo   [keep] %~1 ^(empty^) & del "%~1.new" 2>nul )
exit /b
