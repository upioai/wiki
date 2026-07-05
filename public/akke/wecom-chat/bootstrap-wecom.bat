@echo off
rem Akke WeCom auto-reply (route B) one-time bootstrap for this cloud PC.
rem Installs deps and creates .env from template. ASCII-only (GBK console safe).
chcp 65001 >nul
cd /d "%~dp0"
echo === Akke WeCom auto-reply bootstrap ===
python -m pip install --upgrade pip
pip install pyautogui pygetwindow pillow python-dotenv pyperclip pywin32
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example - EDIT IT now: keys / endpoint / secret / coords.
) else (
  echo .env already exists, not overwriting.
)
echo.
echo Next: edit .env, open WeCom and log in, then run start-wecom-reply.bat
echo Keep AKKE_WECOM_DRY_RUN=1 for the first runs to verify reading/coords.
pause
