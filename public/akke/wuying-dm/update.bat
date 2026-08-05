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
REM DO NOT use caret (^) line-continuation for the [2/3] PowerShell block: this
REM file ships with LF line endings (edited on macOS, curl'd raw to the cloud PC)
REM and cmd.exe's ^ continuation only works with CRLF -- under LF it collapses the
REM multi-line command, breaking the { } block ('missing }') and splitting off
REM $ErrorActionPreference as a bogus command. That was the 2026-07-10 all-fail
REM bug. Keep the powershell -Command on ONE physical line, however long.
REM
REM Usage: double-click. Backs up *.py -> *.bak, downloads the latest PC + web/DOM
REM channel .py from the public mirror (upioai/wiki, token-free), tells you restart.
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
if exist ambig-mute.txt              copy /Y ambig-mute.txt              ambig-mute.txt.bak              >nul
if exist douyin_dm_web_send_dom.py     copy /Y douyin_dm_web_send_dom.py     douyin_dm_web_send_dom.py.bak     >nul
if exist douyin_dm_web_capture_dom.py  copy /Y douyin_dm_web_capture_dom.py  douyin_dm_web_capture_dom.py.bak  >nul
if exist douyin_dm_web_reply.py        copy /Y douyin_dm_web_reply.py        douyin_dm_web_reply.py.bak        >nul
if exist douyin_dm_web_capture.py      copy /Y douyin_dm_web_capture.py      douyin_dm_web_capture.py.bak      >nul
if exist douyin_dm_autoreply.py        copy /Y douyin_dm_autoreply.py        douyin_dm_autoreply.py.bak        >nul
if exist douyin_dm_bubble_capture.py   copy /Y douyin_dm_bubble_capture.py   douyin_dm_bubble_capture.py.bak   >nul
echo     done ^(rollback: rename *.py.bak back to *.py^)
echo.

REM -- [2/3] download ------------------------------------------------------
echo [2/3] Downloading latest from GitHub public mirror ...
REM ONE physical line on purpose -- see caret warning in the header block above.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $base='https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm'; $files=@('wuying_poll_agent.py','douyin_dm_grounded.py','douyin_rc_reply_grounded.py','douyin_comment_grounded.py','douyin_inbox_uia.py','ambig-mute.txt','douyin_dm_web_send_dom.py','douyin_dm_web_capture_dom.py','douyin_dm_web_reply.py','douyin_dm_web_capture.py','douyin_dm_autoreply.py','douyin_dm_bubble_capture.py'); Invoke-WebRequest -Uri \"$base/update.bat\" -OutFile 'update.bat.new' -UseBasicParsing -TimeoutSec 30; foreach ($f in $files) { try { Invoke-WebRequest -Uri \"$base/$f\" -OutFile \".\$f\" -UseBasicParsing -TimeoutSec 30; Write-Host \"     OK   $f\" } catch { Write-Host \"     FAIL $f : $($_.Exception.Message)\"; exit 1 } }"

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

REM -- [2.5/3] self-update check -------------------------------------------
REM The old updater could not learn about newly added files, because its own
REM $files list did not include itself -- a one-way ratchet: a machine's
REM update.bat stayed frozen at whatever day it was ferried over, so every
REM file added to the list afterwards was NEVER downloadable there. Hit three
REM times (2026-07-19, 2026-07-28, 2026-08-05 -- the last one: PR added
REM douyin_dm_bubble_capture.py to the repo list, operator double-clicked,
REM Test-Path still False).
REM
REM DO NOT overwrite update.bat in place while it is running: cmd.exe reads a
REM .bat incrementally by byte offset as it executes, so replacing the running
REM file makes it resume mid-garbage. We download to .new and let the operator
REM swap it after this window closes.
if exist update.bat.new (
    fc /B update.bat update.bat.new >nul 2>&1
    if errorlevel 1 (
        echo ============================================================
        echo   NOTE: the updater itself ^(update.bat^) has a NEW version.
        echo   Saved as update.bat.new -- it is NOT applied yet.
        echo   After this window closes, run ONE command in that folder:
        echo.
        echo       move /Y update.bat.new update.bat
        echo.
        echo   Then double-click update.bat again to pick up any newly
        echo   added scripts. Skipping this means new scripts added later
        echo   will silently never reach this machine.
        echo ============================================================
        echo.
    ) else (
        del /Q update.bat.new >nul 2>&1
    )
)

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
