@echo off
rem Launch the WeCom auto-reply loop with crash-restart.
rem If the loop stops itself on repeated errors it writes wecom_reply_STOP;
rem this launcher then idles (no relaunch) until you remove that file. ASCII-only.
chcp 65001 >nul
cd /d "%~dp0"
:loop
if exist "wecom_reply_STOP" (
  echo [start] STOP flag present - NOT launching. Investigate, then delete wecom_reply_STOP to resume.
  timeout /t 30 /nobreak >nul
  goto loop
)
echo [start] launching wecom_reply_loop.py ...
python wecom_reply_loop.py
echo [start] loop exited; relaunch in 10s (Ctrl+C to abort) ...
timeout /t 10 /nobreak >nul
goto loop
