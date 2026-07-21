@echo off
REM ============================================================================
REM Akke cloud-PC WeCom auto-reply updater (no git; downloads latest over HTTP).
REM
REM ASCII-ONLY ON PURPOSE: cmd.exe reads .bat bytes in the legacy OEM codepage
REM (GBK/936 on Chinese Windows), NOT UTF-8. Any non-ASCII gets garbled into junk
REM tokens cmd tries to RUN. Same reason as wuying-dm/update.bat. Keep it English.
REM
REM DO NOT use caret (^) line-continuation for the PowerShell block: this file
REM ships with LF endings (edited on macOS, curl'd raw to the cloud PC) and cmd's
REM ^ continuation only works with CRLF. Keep the powershell -Command on ONE line.
REM
REM Usage: double-click. Backs up *.py -> *.bak, downloads the latest from the
REM public mirror (upioai/wiki, token-free), then tells you to restart the loop.
REM ============================================================================

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Akke WeCom auto-reply updater
echo   Folder: %CD%
echo ============================================================
echo.

REM -- [1/3] backup --------------------------------------------------------
echo [1/3] Backing up current files to *.bak ...
if exist wecom_reply_loop.py     copy /Y wecom_reply_loop.py     wecom_reply_loop.py.bak     >nul
if exist probe_image_cache.py    copy /Y probe_image_cache.py    probe_image_cache.py.bak    >nul
if exist measure_wecom_coords.py copy /Y measure_wecom_coords.py measure_wecom_coords.py.bak >nul
if exist calib_wecom_vl.py       copy /Y calib_wecom_vl.py       calib_wecom_vl.py.bak       >nul
if exist start-wecom-reply.bat   copy /Y start-wecom-reply.bat   start-wecom-reply.bat.bak   >nul
echo     done ^(rollback: rename *.bak back^)
echo.

REM -- [2/3] download ------------------------------------------------------
echo [2/3] Downloading latest from GitHub public mirror ...
REM ONE physical line on purpose -- see caret warning in the header block above.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $base='https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wecom-chat'; $files=@('wecom_reply_loop.py','probe_image_cache.py','measure_wecom_coords.py','calib_wecom_vl.py','start-wecom-reply.bat'); foreach ($f in $files) { try { Invoke-WebRequest -Uri \"$base/$f\" -OutFile \".\$f\" -UseBasicParsing -TimeoutSec 30; Write-Host \"     OK   $f\" } catch { Write-Host \"     FAIL $f : $($_.Exception.Message)\"; exit 1 } }"

if errorlevel 1 (
    echo.
    echo   Download FAILED. Try: close this window, wait 30s, double-click again.
    echo   Keeps failing - ping PM ^(GitHub may be blocked^). Rollback: rename *.bak back.
    echo.
    pause
    exit /b 1
)

echo     all downloaded
echo.

REM -- [3/3] restart -------------------------------------------------------
echo ============================================================
echo   [3/3] Files updated.
echo   wecom_reply_loop.py is LONG-RUNNING - you MUST restart it:
echo     1. Close the black window running the reply loop
echo     2. Double-click start-wecom-reply.bat
echo   Verify: the new window prints [loop] within ~30s.
echo ============================================================
echo.
pause
endlocal
