@echo off
REM ============================================================================
REM Akke cloud-pc watchdog launcher. Put THIS FILE (or a shortcut) into the
REM startup folder (Win+R -> shell:startup) so it runs at every logon and
REM keeps DOM DM + route-B alive unattended. Close the window to stop.
REM
REM Hardcoded path on purpose: a copy in shell:startup must still find the
REM real script dir. All cloud PCs standardize on C:\akke-wuying\wuying-dm.
REM ASCII-ONLY on purpose: cmd.exe reads .bat in the legacy OEM codepage,
REM any Chinese in a .bat gets garbled into junk tokens cmd tries to RUN.
REM ============================================================================
title Akke-watchdog
powershell -ExecutionPolicy Bypass -NoProfile -File "C:\akke-wuying\wuying-dm\watchdog-dm-routeb.ps1"
pause
