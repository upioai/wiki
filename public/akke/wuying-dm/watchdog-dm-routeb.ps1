# ============================================================================
# Akke cloud-pc watchdog : keep DOM DM + route-B alive, unattended.
#   Put watchdog-dm-routeb.bat in shell:startup -> this runs at every logon.
#   First cycle (after a boot-readiness delay) launches everything; then it
#   patrols every 5 min and revives whatever died. Closing this window = stop.
#
# What it manages (all machines now run the DOM combo, no douyin PC client):
#   1. Edge   : VISIBLE window (headless triggers douyin captcha - do not use),
#               CDP :9222, profile C:\akke-edge-debug, douyin.com tab.
#               Dead/zombie (port silent) -> kill akke-edge-debug msedge, relaunch.
#   2. DM     : wuying_poll_agent.py, launched with output teed to agent.log
#               (capture leg prints every ~3min even when idle -> agent.log
#               mtime is a heartbeat). Missing -> relaunch. Alive but agent.log
#               stale > $FreezeMin -> frozen: kill THAT pid only, relaunch.
#   3. routeB : _start-realtime-touch.bat spawns 2 cmd loop-windows (watch /
#               consume) which already self-restart their python on crash.
#               Watchdog only heals what the loops cannot:
#                 - a loop WINDOW gone (closed / after reboot) -> kill both
#                   sides' cmd+python, rerun the parent bat (pair restart);
#                 - watch python frozen (realtime-touch.log stale) -> kill that
#                   python only, its cmd loop revives it in 10s.
#               consume gets NO freeze check: it is event-driven, a long quiet
#               queue is normal - log staleness would false-kill it.
#
# Safety:
#   - STOP file on the Desktop (STOP.txt) pauses ALL actions until removed
#     (manual maintenance / cookie relogin without the watchdog fighting you).
#   - Kills are always per-PID by exact command line. NEVER Stop-Process python
#     wholesale (iron rule: that killed route-B along with DM in the past).
#   - Login expiry is NOT detected here (low-freq, sentinels + human relogin).
#   - Single instance: a second watchdog exits immediately.
#
# Env for children: AKKE_WINDOW_LOCK=1 (DM/route-B serialize on the CDP tab,
# DM first), AKKE_REVERSE_COMMENT_ENABLED=0 (RC not wired into the lock).
# ============================================================================
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

$IntervalSec   = 300     # patrol every 5 min
$FirstDelaySec = 60      # boot readiness wait before the first cycle
$FreezeMin     = 30      # heartbeat-file staleness => frozen (idle prints are ~3min/10min)
$EdgeProfile   = 'C:\akke-edge-debug'
$CdpUrl        = 'http://127.0.0.1:9222/json/version'
$WatchdogLog   = Join-Path $PSScriptRoot 'watchdog.log'
$AgentLog      = Join-Path $PSScriptRoot 'agent.log'
$RouteBLog     = Join-Path $PSScriptRoot 'realtime-touch.log'
$StopFile      = Join-Path ([Environment]::GetFolderPath('Desktop')) 'STOP.txt'

function Log([string]$msg) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8
}

# --- single instance guard -------------------------------------------------
$dup = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.Name -match '^(powershell|pwsh)' -and
    $_.CommandLine -like '*watchdog-dm-routeb.ps1*'
}
if ($dup) { Write-Host "another watchdog is running (pid $($dup[0].ProcessId)) - exiting."; exit }

# rotate watchdog.log at 10MB (Add-Content reopens per write, rename is safe here)
if ((Test-Path $WatchdogLog) -and (Get-Item $WatchdogLog).Length -gt 10MB) {
    Move-Item -Force $WatchdogLog "$WatchdogLog.1"
}

# env inherited by every child launched below
$env:AKKE_WINDOW_LOCK = '1'
$env:AKKE_REVERSE_COMMENT_ENABLED = '0'

$PyCmd = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }

# clear stale window-lock markers once at startup (crash leftovers; lock self-heals in 5min)
Remove-Item -ErrorAction SilentlyContinue '.douyin-win.lock', '.douyin-dm-want'

Log "=== watchdog up (pid $PID, patrol ${IntervalSec}s, freeze ${FreezeMin}min, py=$PyCmd) ==="
Log "STOP switch: create $StopFile to pause; close this window to stop for good."
Start-Sleep -Seconds $FirstDelaySec

# --- helpers ---------------------------------------------------------------
function Get-ProcByCmdline([string]$namePattern, [string]$cmdPattern) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match $namePattern -and $_.CommandLine -like $cmdPattern
    }
}

function Test-Frozen($proc, [string]$logPath) {
    # frozen = heartbeat file stale AND the process itself is old enough that
    # "just started, no line yet" cannot explain the silence.
    if (-not (Test-Path $logPath)) { return $false }
    $stale = ((Get-Date) - (Get-Item $logPath).LastWriteTime).TotalMinutes -gt $FreezeMin
    $old   = ((Get-Date) - $proc.CreationDate).TotalMinutes -gt $FreezeMin
    return ($stale -and $old)
}

function Start-EdgeDebug {
    $edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    if (-not (Test-Path $edge)) { $edge = 'C:\Program Files\Microsoft\Edge\Application\msedge.exe' }
    # VISIBLE on purpose (headless => douyin captcha). Minimized best-effort.
    Start-Process $edge -WindowStyle Minimized -ArgumentList `
        '--remote-debugging-port=9222', "--user-data-dir=$EdgeProfile", 'https://www.douyin.com'
}

function Start-PollAgent {
    # -u: unbuffered so Tee flushes promptly -> agent.log mtime works as heartbeat.
    if ((Test-Path $AgentLog) -and (Get-Item $AgentLog).Length -gt 20MB) {
        Move-Item -Force $AgentLog "$AgentLog.1"   # rotate while no tee holds it
    }
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList `
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', `
        "`$host.UI.RawUI.WindowTitle='Akke-DM-agent'; & $PyCmd -u wuying_poll_agent.py 2>&1 | Tee-Object -FilePath agent.log -Append"
}

# --- patrol ----------------------------------------------------------------
while ($true) {
    try {
        if (Test-Path $StopFile) {
            Log 'STOP.txt on Desktop -> paused (delete it to resume).'
            Start-Sleep -Seconds $IntervalSec; continue
        }
        $acted = @()

        # (1) Edge: CDP must answer; a debug-profile msedge that does not answer
        #     is a zombie holding the port/profile -> kill it before relaunch.
        $cdpOk = $false
        try { Invoke-RestMethod -Uri $CdpUrl -TimeoutSec 5 | Out-Null; $cdpOk = $true } catch {}
        if (-not $cdpOk) {
            $zombies = Get-ProcByCmdline '^msedge' "*$EdgeProfile*"
            foreach ($z in $zombies) { Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue }
            if ($zombies) { $acted += "edge-zombie-killed($($zombies.Count))" }
            Start-EdgeDebug
            $acted += 'edge-relaunched'
            Start-Sleep -Seconds 15
        }

        # (2) DM poll agent
        $agent = Get-ProcByCmdline '^(py|python)' '*wuying_poll_agent*'
        if ($agent -and (Test-Frozen $agent[0] $AgentLog)) {
            Log "DM agent pid $($agent[0].ProcessId) frozen (agent.log stale > $FreezeMin min) -> kill + relaunch"
            Stop-Process -Id $agent[0].ProcessId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            $agent = $null
        }
        if (-not $agent) {
            Start-PollAgent
            $acted += 'dm-agent-launched'
        } elseif ($agent -and -not (Test-Path $AgentLog)) {
            Log 'DM agent alive but no agent.log (manual launch without tee?) - freeze check inactive.'
        }

        # (3) route-B: the two cmd loop-windows must both exist.
        if (Test-Path '_start-realtime-touch.bat') {
            $watchLoop   = Get-ProcByCmdline '^cmd' '*_start-realtime-touch.bat* watch*'
            $consumeLoop = Get-ProcByCmdline '^cmd' '*_start-realtime-touch.bat* consume*'
            $watchPy     = Get-ProcByCmdline '^(py|python)' '*_realtime_touch_watch_wuying*'
            $consumePy   = Get-ProcByCmdline '^(py|python)' '*_realtime_touch_consume_wuying*'

            if (-not $watchLoop -or -not $consumeLoop) {
                # pair restart: kill any survivors (cmd loops AND their pythons -
                # killing only cmd would orphan a running python), rerun parent bat.
                foreach ($p in @($watchLoop) + @($consumeLoop) + @($watchPy) + @($consumePy)) {
                    if ($p) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
                }
                Start-Sleep -Seconds 3
                Start-Process cmd -WorkingDirectory $PSScriptRoot -ArgumentList '/c', '_start-realtime-touch.bat'
                $acted += 'routeb-pair-restarted'
            } elseif ($watchPy -and (Test-Frozen $watchPy[0] $RouteBLog)) {
                # frozen watch: kill the python only - its cmd loop revives it in 10s.
                Log "route-B watch pid $($watchPy[0].ProcessId) frozen (realtime-touch.log stale) -> kill (loop revives)"
                Stop-Process -Id $watchPy[0].ProcessId -Force -ErrorAction SilentlyContinue
                $acted += 'routeb-watch-unfroze'
            }
        } else {
            Log 'no _start-realtime-touch.bat here -> route-B not managed (DM only). Ferry route-B files first.'
        }

        if ($acted.Count) { Log ('cycle: ' + ($acted -join ', ')) }
        else { Log 'cycle: all alive (edge cdp ok, dm agent ok, route-B ok)' }
    } catch {
        Log "cycle error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
