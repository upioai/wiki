@echo off
REM ============================================================================
REM Akke cloud-PC agent updater (no git needed; downloads latest .py over HTTP).
REM
REM ASCII-ONLY ON PURPOSE: cmd.exe reads .bat bytes in the legacy OEM codepage
REM (GBK/936 on Chinese Windows), NOT UTF-8. Any non-ASCII (Chinese) in a .bat
REM gets garbled into junk tokens cmd tries to RUN ('xxx is not a command') --
REM that was exactly the 2026-06-28 garble bug. Keep this file English-only.
REM Mirror start-dm-routeb.bat which is ASCII-only for the same reason.
REM
REM Usage: double-click. Backs up *.py -> *.bak, downloads the latest 5 .py from
REM the public mirror (upioai/wiki, token-free), then tells you about restart.
REM ============================================================================

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Akke cloud-PC agent updater
echo   Folder: %CD%
echo ============================================================
echo.

REM -- [1/3] backup --------------------------------------------------------
echo [1/3] Backing up current files to *.bak ...
if exist wuying_poll_agent.py        copy /Y wuying_poll_agent.py        wuying_poll_agent.py.bak        >nul
if exist douyin_dm_grounded.py       copy /Y douyin_dm_grounded.py       douyin_dm_grounded.py.bak       >nul
if exist douyin_rc_reply_grounded.py copy /Y douyin_rc_reply_grounded.py douyin_rc_reply_grounded.py.bak >nul
if exist douyin_comment_grounded.py  copy /Y douyin_comment_grounded.py  douyin_comment_grounded.py.bak  >nul
if exist douyin_inbox_uia.py         copy /Y douyin_inbox_uia.py         douyin_inbox_uia.py.bak         >nul
if exist wecom_add_contact_grounded.py copy /Y wecom_add_contact_grounded.py wecom_add_contact_grounded.py.bak >nul
if exist douyin_dm_web_send_dom.py     copy /Y douyin_dm_web_send_dom.py     douyin_dm_web_send_dom.py.bak     >nul
if exist douyin_dm_web_capture_dom.py  copy /Y douyin_dm_web_capture_dom.py  douyin_dm_web_capture_dom.py.bak  >nul
if exist douyin_dm_web_reply.py        copy /Y douyin_dm_web_reply.py        douyin_dm_web_reply.py.bak        >nul
if exist douyin_dm_web_capture.py      copy /Y douyin_dm_web_capture.py      douyin_dm_web_capture.py.bak      >nul
if exist douyin_dm_autoreply.py        copy /Y douyin_dm_autoreply.py        douyin_dm_autoreply.py.bak        >nul
echo     done ^(rollback: rename *.py.bak back to *.py^)
echo.

REM -- [2/3] download ------------------------------------------------------
echo [2/3] Downloading latest from GitHub public mirror ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { ^
  $ErrorActionPreference = 'Stop'; ^
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
  $base = 'https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm'; ^
  $files = @('wuying_poll_agent.py','douyin_dm_grounded.py','douyin_rc_reply_grounded.py','douyin_comment_grounded.py','douyin_inbox_uia.py','wecom_add_contact_grounded.py','douyin_dm_web_send_dom.py','douyin_dm_web_capture_dom.py','douyin_dm_web_reply.py','douyin_dm_web_capture.py','douyin_dm_autoreply.py'); ^
  foreach ($f in $files) { ^
    try { Invoke-WebRequest -Uri \"$base/$f\" -OutFile \".\$f\" -UseBasicParsing -TimeoutSec 30; Write-Host \"     OK   $f\" } ^
    catch { Write-Host \"     FAIL $f : $($_.Exception.Message)\"; exit 1 } ^
  } ^
}"

if errorlevel 1 (
    echo.
    echo   Download FAILED. Try: close this window, wait 30s, double-click update.bat again.
    echo   Keeps failing - ping PM ^(GitHub may be blocked^). Rollback: rename *.py.bak to *.py
    echo.
    pause
    exit /b 1
)

echo     all downloaded
echo.

REM -- [3/3] restart note --------------------------------------------------
echo ============================================================
echo   [3/3] Files updated.
echo   The DM sender ^(douyin_dm_grounded.py^) reloads fresh on every
echo   batch, so the next send uses the new version automatically --
echo   NO restart needed for DM-sender-only changes.
echo   If the long-running agent ^(wuying_poll_agent.py^) changed, close
echo   its black window and restart it ^(or reboot the cloud PC^) to be safe.
echo ============================================================
echo.
pause
endlocal
