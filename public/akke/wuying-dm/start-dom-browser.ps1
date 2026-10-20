# Run in the account owner's interactive session. Does not log in or send anything.
param([string]$ProfilePath = 'C:\akke-edge-dom-sz')
$ErrorActionPreference = 'Stop'
try {
    $null = Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 3
    exit 0
} catch { }
$edge = @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
          'C:\Program Files\Microsoft\Edge\Application\msedge.exe') |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'Edge not installed' }
Start-Process -FilePath $edge -WindowStyle Minimized -ArgumentList @(
    '--remote-debugging-port=9222', '--remote-debugging-address=127.0.0.1',
    "--user-data-dir=`"$ProfilePath`"", '--no-first-run', 'https://www.douyin.com/jingxuan'
)
