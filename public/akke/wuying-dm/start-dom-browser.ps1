# Run in the account owner's interactive session. Does not log in or send anything.
# Liveness needs a real CDP round trip: debug Edge can hang so that /json/version answers and the
# websocket connects but commands never reply. playwright connect_over_cdp then times out (180s),
# DOM capture stalls, and a /json/version-only check keeps exiting 0 (Shenzhen machine, 2026-10-27, 4.5h).
# Probe: /json/version -> browser ws -> Browser.getVersion -> attach each page/iframe + Runtime.evaluate.
# After $FailsBeforeRestart consecutive failures, kill this profile's msedge by --user-data-dir and start
# again (Edge allows one instance per profile; Start-Process without killing just hands off to the zombie).
# Events go to logs\dom-browser-watchdog.log. Keep this file ASCII: PowerShell 5.1 reads BOM-less files as ANSI.
param([string]$ProfilePath = 'C:\akke-edge-dom-sz', [int]$Port = 9222,
      [int]$ProbeTimeoutSec = 15, [int]$FailsBeforeRestart = 2)
$ErrorActionPreference = 'Stop'
$logDir = Join-Path $PSScriptRoot 'logs'
$tag = ($ProfilePath -replace '[^A-Za-z0-9]', '_')
$failFile = Join-Path $logDir "dom-browser-fails-$tag.txt"
function Log([string]$m) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Add-Content -Path (Join-Path $logDir 'dom-browser-watchdog.log') -Encoding UTF8 `
        -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$ProfilePath :$Port] $m"
}

function Send-Cdp($ws, [int]$id, [string]$method, [hashtable]$params, [string]$sessionId, [datetime]$deadline) {
    $msg = @{ id = $id; method = $method; params = $params }
    if ($sessionId) { $msg.sessionId = $sessionId }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($msg | ConvertTo-Json -Compress -Depth 5))
    $seg = [ArraySegment[byte]]::new($bytes)
    $ct = [Threading.CancellationToken]::None
    if (-not $ws.SendAsync($seg, 'Text', $true, $ct).Wait(5000)) { throw "send $method timeout" }
    $buf = New-Object byte[] 65536
    while ($true) {
        $ms = New-Object IO.MemoryStream
        do {
            $left = [int]($deadline - (Get-Date)).TotalMilliseconds
            if ($left -le 0) { throw "$method no reply" }
            $t = $ws.ReceiveAsync([ArraySegment[byte]]::new($buf), $ct)
            if (-not $t.Wait($left)) { throw "$method no reply" }
            if ($t.Result.MessageType -eq 'Close') { throw "ws closed during $method" }
            $ms.Write($buf, 0, $t.Result.Count)
        } while (-not $t.Result.EndOfMessage)
        $r = [Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json
        if ($r.id -eq $id) {
            if ($r.error) { throw "$method error: $($r.error.message)" }
            return $r.result
        }
    }
}

# $null = alive, otherwise the reason it is not
function Test-Cdp {
    try { $v = Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 3 }
    catch { return "json/version: $($_.Exception.Message)" }
    $deadline = (Get-Date).AddSeconds($ProbeTimeoutSec)
    $ws = New-Object Net.WebSockets.ClientWebSocket
    try {
        $ct = [Threading.CancellationToken]::None
        if (-not $ws.ConnectAsync([Uri]$v.webSocketDebuggerUrl, $ct).Wait(5000)) { return 'ws connect timeout' }
        $null = Send-Cdp $ws 1 'Browser.getVersion' @{} $null $deadline
        $targets = (Send-Cdp $ws 2 'Target.getTargets' @{} $null $deadline).targetInfos |
            Where-Object { $_.type -in @('page', 'iframe') } | Select-Object -First 8
        $id = 10
        foreach ($t in $targets) {
            $s = (Send-Cdp $ws ($id++) 'Target.attachToTarget' @{ targetId = $t.targetId; flatten = $true } $null $deadline).sessionId
            try { $null = Send-Cdp $ws ($id++) 'Runtime.evaluate' @{ expression = '1'; returnByValue = $true } $s $deadline }
            catch { return "$($t.type) $($t.url): $($_.Exception.Message)" }
            $null = Send-Cdp $ws ($id++) 'Target.detachFromTarget' @{ sessionId = $s } $null $deadline
        }
        return $null
    } catch {
        $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
        return $e.Message
    } finally { $ws.Dispose() }
}

function Get-ProfileEdge {
    $p = '--user-data-dir="?' + [regex]::Escape($ProfilePath) + '"?(\s|$)'
    @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { $_.CommandLine -match $p })
}

$why = Test-Cdp
if (-not $why) { Remove-Item $failFile -ErrorAction SilentlyContinue; exit 0 }
$procs = Get-ProfileEdge
if ($procs.Count -gt 0) {
    $fails = 1 + [int](Get-Content $failFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($fails -lt $FailsBeforeRestart) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        Set-Content -Path $failFile -Value $fails
        Log "probe fail $fails/$FailsBeforeRestart, waiting: $why"
        exit 0
    }
    Log "probe fail $fails/$FailsBeforeRestart, killing $($procs.Count) msedge: $why"
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 15 -and (Get-ProfileEdge).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
} else {
    Log "no Edge for profile, starting: $why"
}
Remove-Item $failFile -ErrorAction SilentlyContinue
$edge = @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
          'C:\Program Files\Microsoft\Edge\Application\msedge.exe') |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'Edge not installed' }
Start-Process -FilePath $edge -WindowStyle Minimized -ArgumentList @(
    "--remote-debugging-port=$Port", '--remote-debugging-address=127.0.0.1',
    "--user-data-dir=`"$ProfilePath`"", '--no-first-run', 'https://www.douyin.com/jingxuan'
)
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try { $v = Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 2; Log "started: $($v.webSocketDebuggerUrl)"; exit 0 } catch { }
}
Log 'started but json/version still down after 30s'
