@echo off
REM ============================================================
REM Akke potential-touch route-B resident launcher (Wuying cloud PC)
REM Double-click to start. Opens 2 windows: watch + consume.
REM Each auto-restarts on crash (10s). Stop: close both windows.
REM
REM ASCII-only on purpose: cmd.exe garbles UTF-8 Chinese in .bat
REM (the .py scripts read UTF-8 themselves and are fine).
REM
REM Prereq files in this dir (C:\akke-wuying\wuying-dm\):
REM   _realtime_touch_watch_wuying.py   _probe_follow_feed_wuying.py
REM   _realtime_touch_consume_wuying.py _jit_comment1_wuying.py
REM   dy_cookie.txt   special-follow.json   realtime-touch-table.json
REM   douyin_comment_grounded.py    .env (needs OPENROUTER_API_KEY)
REM
REM Before each boot: Douyin PC logged-in + maximized + foreground;
REM   resolution 2560x1600; IME = English.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if "%1"=="watch"   goto watchloop
if "%1"=="consume" goto consumeloop

REM --- self-update: pull latest route-B code from public mirror (token-free) ---
REM Only the parent (no-arg) launch does this once per double-click; the watch/
REM consume sub-windows skip it via the gotos above. SAFE: download to .new and
REM swap in only if curl succeeded AND the file is non-empty -- a mirror blip or
REM 404 must never overwrite a working local copy. Data files / .env / cookie are
REM never touched. Updating THIS .bat itself needs a manual re-deploy (Step 0).
echo === self-update: pull latest scripts from public mirror ===
set "MIRROR=https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm"
call :pull _realtime_touch_consume_wuying.py
call :pull _realtime_touch_watch_wuying.py
call :pull _jit_comment1_wuying.py
call :pull _probe_follow_feed_wuying.py
call :pull _follow_grounded.py
call :pull douyin_comment_grounded.py
echo === self-update done ===
echo.

echo === Akke route-B resident: watch + consume ===
echo Launching two windows (each auto-restarts). Close them to stop.
echo Keep Douyin PC logged-in + maximized + foreground. Do NOT touch mouse/keyboard during sends.
echo.
start "Akke-watch" cmd /c ""%~f0" watch"
timeout /t 2 >nul
start "Akke-consume" cmd /c ""%~f0" consume"
echo Two windows launched. You can close THIS window.
timeout /t 5 >nul
exit /b

REM --- helper: safely refresh one file from %MIRROR% (keep local on any failure) ---
:pull
curl.exe -fsS "%MIRROR%/%~1" -o "%~1.new"
if errorlevel 1 ( echo   [keep] %~1 ^(fetch failed^) & del "%~1.new" 2>nul & exit /b )
for %%S in ("%~1.new") do if %%~zS GTR 0 ( move /y "%~1.new" "%~1" >nul & echo   [ok]   %~1 ) else ( echo   [keep] %~1 ^(empty^) & del "%~1.new" 2>nul )
exit /b

:watchloop
echo === [watch] monitor follow-feed, catch lead new videos ^(under 20min^) ===
py _realtime_touch_watch_wuying.py --pool special-follow.json --max-age-min 20
echo [watch exited, restart in 10s] & timeout /t 10 >nul
goto watchloop

:consumeloop
echo === [consume] tail queue, JIT comment1 + table comment2, send 3-combo ===
py _realtime_touch_consume_wuying.py
echo [consume exited, restart in 10s] & timeout /t 10 >nul
goto consumeloop
