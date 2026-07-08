@echo off
REM route-B 一键启动：弹两个窗口(watch 侦测 + consume 消费·浏览器DOM)。
REM 前置：poll agent 已在跑、隐形 Edge 已登录、.env 有 AKKE_WINDOW_LOCK=1。
cd /d C:\akke-wuying\wuying-dm

start "routeB-watch" cmd /k "py _realtime_touch_watch_wuying.py --cookie-file dy_cookie.txt --max-age-min 20 --interval-sec 60"

timeout /t 3 /nobreak >/dev/null

start "routeB-consume" cmd /k "py _realtime_touch_consume_wuying.py --comment-script douyin_touch_web_dom.py --db"

echo.
echo route-B 两窗已弹出（routeB-watch / routeB-consume）。
echo watch 里看 lead池 是否 ^>0；关本窗口不影响那两个。
echo.
pause
