$ErrorActionPreference = "SilentlyContinue"
Set-Location "C:\akke-wuying\wuying-dm"

# --- self-update: pull latest wuying_poll_agent.py from public mirror (best-effort) ---
# Pulls to .new, keeps only if size looks sane (>20KB, guards truncated download),
# backs up current to .bak, else keeps local. Never blocks startup on failure.
try {
    $u = "https://raw.githubusercontent.com/upioai/wiki/main/public/akke/wuying-dm/wuying_poll_agent.py"
    Invoke-WebRequest -UseBasicParsing $u -OutFile "wuying_poll_agent.py.new" -TimeoutSec 20
    if ((Get-Item "wuying_poll_agent.py.new").Length -gt 20000) {
        if (Test-Path "wuying_poll_agent.py") { Copy-Item -Force "wuying_poll_agent.py" "wuying_poll_agent.py.bak" }
        Move-Item -Force "wuying_poll_agent.py.new" "wuying_poll_agent.py"
        Write-Host "[self-update] wuying_poll_agent.py updated" -ForegroundColor Green
    } else {
        Remove-Item -Force "wuying_poll_agent.py.new" -ErrorAction SilentlyContinue
        Write-Host "[self-update] download too small, kept local version" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[self-update] pull failed (GitHub blocked?), kept local version" -ForegroundColor Yellow
}

Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like "*wuying_poll_agent*"} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process | Where-Object {$_.Name -eq "msedge.exe" -and $_.CommandLine -like "*akke-edge-debug*"} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 2
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (!(Test-Path $edge)) { $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe" }
Start-Process $edge -ArgumentList "--remote-debugging-port=9222","--user-data-dir=C:\akke-edge-debug","https://www.douyin.com"
$ok = $false
for ($i=0; $i -lt 15; $i++) { Start-Sleep 2; try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:9222/json/version" -TimeoutSec 2 | Out-Null; $ok=$true; break } catch {} }
if ($ok) { Write-Host "Edge CDP ready" -ForegroundColor Green } else { Write-Host "WARN: Edge CDP not ready" -ForegroundColor Yellow }
Start-Sleep 15
Write-Host "Warm-up capture..." -ForegroundColor Cyan
py douyin_dm_web_capture_dom.py
Start-Process cmd -ArgumentList "/k py wuying_poll_agent.py" -WorkingDirectory (Get-Location)
if (Test-Path "_start-realtime-touch.bat") { Start-Process cmd -ArgumentList "/c _start-realtime-touch.bat" -WorkingDirectory (Get-Location); Write-Host "route-B launched -- CHECK Douyin client is foreground+logged+maximized" -ForegroundColor Yellow }
Write-Host "DONE." -ForegroundColor Cyan
