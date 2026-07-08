@echo off
REM route-B 一键启动：弹两个窗口(watch 侦测 + consume 消费·浏览器DOM)。
REM 前置：poll agent 已在跑、隐形 Edge 已登录、.env 有 AKKE_WINDOW_LOCK=1。
cd /d C:\akke-wuying\wuying-dm

start "routeB-watch" cmd /k "py _realtime_touch_watch_wuying.py --pool special-follow.json --cookie-file dy_cookie.txt --max-age-min 10 --interval-sec 60"

timeout /t 3 /nobreak >/dev/null

start "routeB-consume" cmd /k "py _realtime_touch_consume_wuying.py --comment-script douyin_touch_web_dom.py --pool special-follow.json --table realtime-touch-table.json"

echo.
echo route-B 两个窗口已弹出（routeB-watch / routeB-consume）。
echo 看 watch 窗口有没有报 cookie/签名错；关掉本窗口不影响那两个。
echo.
pause
