@echo off
REM ============================================================================
REM Akke 云电脑 · DM + round-b(潜在触达 route-B) 同机【串行】启动器
REM   —— 只给开了窗口锁的机器用（目前 = 小文/野荞）。双击即可。
REM
REM 一台无影只有一个抖音前台窗口。本启动器把两条通道一起拉起：
REM   - AKKE_WINDOW_LOCK=1   两个执行器在窗口层串行、DM 优先（见 wuying_window_lock.py）
REM   - AKKE_REVERSE_COMMENT_ENABLED=0  关 RC —— 窗口锁没 wire 进 RC 执行器，
REM     RC 一旦有单会和 route-B 抢窗口，所以 DM+route-B 串行时强制关 RC。
REM
REM 前提：抖音 PC 已登录 + 最大化 + 在前台；输入法英文；.env 已配好(AKKE_ACCOUNT_ID 等)。
REM 停止：关掉弹出的几个黑窗口即可。
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"

REM 关键 env：下面 start 出来的子窗口都会继承 --------------------------------------
set AKKE_WINDOW_LOCK=1
set AKKE_REVERSE_COMMENT_ENABLED=0

REM 清掉可能残留的死锁标记（上次崩溃没释放；锁本身 5min 自愈，这里提前清干净）----
if exist ".douyin-win.lock" del /q ".douyin-win.lock"
if exist ".douyin-dm-want" del /q ".douyin-dm-want"

echo === 启动 DM + round-b 串行（窗口锁 ON，DM 优先，RC OFF）===
echo 抖音 PC 请保持登录 + 最大化 + 在前台；输入法英文。
echo 弹出的几个黑窗口保持打开别关；停止请逐个关窗口。
echo.

REM ① DM 通道：poll agent claim dispatch_queue -> douyin_dm_grounded -----------------
REM    本机直起 poll agent(没有 start.bat)。启动 header 应显示 rc=off。
start "Akke DM" cmd /k python wuying_poll_agent.py

REM ② round-b 通道：实时监控 + 消费三连（watch + consume 两窗口由它自己拉起）--------
if exist "_start-realtime-touch.bat" (
    start "Akke route-B" cmd /k _start-realtime-touch.bat
) else (
    echo [警告] 没找到 _start-realtime-touch.bat —— route-B 没启动，只跑了 DM。
    echo        这台机器还没摆渡 route-B 脚本？
)

echo.
echo 两条通道已拉起。本窗口可以关闭，子窗口请保持打开。
pause >nul
