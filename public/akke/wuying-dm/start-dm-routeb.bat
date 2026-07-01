@echo off
REM ============================================================================
REM Akke Wuying cloud PC: DM + route-B (potential touch) SERIAL launcher.
REM Double-click to start. For machines with the window lock (one Douyin window).
REM
REM ASCII-ONLY on purpose: cmd.exe reads .bat bytes in the legacy OEM codepage
REM (GBK/936 on Chinese Windows), NOT UTF-8 -- any Chinese in a .bat gets garbled
REM into junk tokens that cmd tries to RUN ('xxx is not a command'). So keep this
REM file English-only. The .py scripts read UTF-8 themselves and print Chinese fine.
REM
REM One Wuying box has a single foreground Douyin window. This launcher starts both:
REM   - AKKE_WINDOW_LOCK=1            DM / consume / follow serialize on the window,
REM                                   DM first (see wuying_window_lock.py).
REM   - AKKE_REVERSE_COMMENT_ENABLED=0  RC off -- the window lock is not wired into
REM                                   the RC executor, so RC would fight route-B.
REM
REM Prereq: Douyin PC logged-in + maximized + foreground; IME English; .env set
REM   (AKKE_ACCOUNT_ID etc). Stop: close the popped-up black windows.
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"

REM Key env -- inherited by the child windows started below.
set AKKE_WINDOW_LOCK=1
set AKKE_REVERSE_COMMENT_ENABLED=0

REM Clear stale lock markers (crash may have left them; lock self-heals in 5min).
if exist ".douyin-win.lock" del /q ".douyin-win.lock"
if exist ".douyin-dm-want" del /q ".douyin-dm-want"

echo === start DM + route-B serial (window lock ON, DM first, RC OFF) ===
echo Keep Douyin PC logged-in + maximized + foreground; IME English.
echo Keep the popped-up black windows open; close them one by one to stop.
echo.

REM (1) DM channel: poll agent claims dispatch_queue -> douyin_dm_grounded.
start "Akke DM" cmd /k python wuying_poll_agent.py

REM (2) route-B: realtime watch + consume 3-combo (it opens its own 2 windows).
if exist "_start-realtime-touch.bat" (
    start "Akke route-B" cmd /k _start-realtime-touch.bat
) else (
    echo [WARN] _start-realtime-touch.bat not found -- route-B NOT started, DM only.
)

echo.
echo Both channels launched. You can close THIS window; keep the child windows open.
pause >nul
