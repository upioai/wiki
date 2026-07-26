$ErrorActionPreference='Continue'
Set-Location C:\akke-wuying\wuying-dm

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

Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wuying_poll_agent*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { $_.CommandLine -like '*akke-edge-debug*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
$edge='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if(!(Test-Path $edge)){ $edge='C:\Program Files\Microsoft\Edge\Application\msedge.exe' }
Start-Process $edge -ArgumentList '--remote-debugging-port=9222','--user-data-dir=C:\akke-edge-debug','https://www.douyin.com'
$ok=$false
foreach($i in 1..30){ try{ Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 2 | Out-Null; $ok=$true; break }catch{ Start-Sleep 2 } }
if(-not $ok){ Write-Host 'ERROR: CDP 9222 not reachable'; exit 1 }
Write-Host 'CDP up, warm-up 15s...'
Start-Sleep 15
py douyin_dm_web_capture_dom.py
Start-Process cmd -ArgumentList '/k','cd /d C:\akke-wuying\wuying-dm && py -u wuying_poll_agent.py'
Write-Host 'DONE: poll agent window launched'
