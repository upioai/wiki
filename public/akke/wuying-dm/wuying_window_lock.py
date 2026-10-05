# -*- coding: utf-8 -*-
"""单抖音窗口 · DM 优先的跨进程串行锁 —— route-B 三连 与 DM 发送 同机共存用。

仅在 env AKKE_WINDOW_LOCK=1 时生效；否则全部 no-op（没设这个 flag 的机器零影响）。

背景：一台无影只有一个抖音前台。DM(douyin_dm_grounded) 和 route-B 三连(douyin_comment_grounded)
是两个独立进程，都用 SetForegroundWindow/SendInput 驱动这个窗口，并发会互相抢焦点、混输入。
本锁让它们在窗口层【串行】，且【DM 优先】：
  - DM 一个发送批(一个 main() 调用)期间置 .dm-want → route-B 不再开新三连；
  - 谁操作窗口前先抢 .douyin-win.lock，操作完释放；
  - route-B 抢锁前先等 .dm-want 清除 → DM 一有单，route-B 在当前这个三连发完后立即让位
    （DM 最坏等 ~一个三连 2.4min）。

文件(同脚本目录)：.douyin-win.lock(持有标记) / .douyin-dm-want(DM 占用标记)。
崩溃兜底：标记 mtime 超 STALE_SEC 未刷新 = 持有者已死，可抢占清除。

用法：
  import wuying_window_lock as wl
  # DM 端 (douyin_dm_grounded.main)：
  with wl.dm_batch():
      for c in contacts:
          wl.dm_keepalive()                 # 刷新 .dm-want mtime 防误判 stale
          with wl.window_turn('dm'):
              process(c)
  # route-B 端 (douyin_comment_grounded.main)：
  for c in contacts:
      with wl.window_turn('rb'):            # DM 在忙则先在这儿等
          process(c, auto)

── 全机 GUI 锁（2026-09-30，env AKKE_GUI_LOCK=1 才生效，与 AKKE_WINDOW_LOCK 互相独立）──
上面那把只管「抖音脚本之间」。同一台无影上还跑着 wecom-chat 仓的企微代回 loop 和 Winds
个微客户端时，三家都动真鼠标键盘、互不认锁 → 回复打进别人的窗口且不报错。
AKKE_GUI_LOCK=1 时 window_turn 在拿到抖音窗口锁之后，再以【低优先级】去拿全机锁
`C:\\ProgramData\\akke-gui\\gui.lock`：微信侧挂着新鲜的 want.* 时抖音不开新回合（当前回合做完
才让位），等锁有上限（默认 600s，env AKKE_GUI_LOCK_WAIT_SEC），等不到抛 GuiLockTimeout，
调用方把这条记成 aborted（没动 GUI、回池可重发、不记账号失败）。
协议、阈值与依据的权威副本在 Akke-AI/wecom-chat `cloudpc/windows-weixin/gui_lock.py`
头注释；下面 >>> 到 <<< 是它那一段的逐字拷贝，改协议两个仓一起改。
不经 window_turn、但也动抖音窗口的子进程（收件箱捕获 / 气泡捕获）用 gui_turn 只拿全机锁。
加锁顺序固定「抖音窗口锁 → 全机锁」，任何持全机锁的一方都不会再去等抖音窗口锁 → 不会死锁。
"""
import os
import time
import threading
import contextlib

ENABLED = os.environ.get("AKKE_WINDOW_LOCK") == "1"
_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCK = os.path.join(_DIR, ".douyin-win.lock")
_DMWANT = os.path.join(_DIR, ".douyin-dm-want")
STALE_SEC = 300   # 标记超 5min 未刷新 = 持有者已死(单个三连~2.4min/单条DM<1.5min,5min 留足余量)
_POLL = 0.5


def _fresh(path):
    try:
        return (time.time() - os.path.getmtime(path)) < STALE_SEC
    except OSError:
        return False


def _try_create(path):
    """原子独占创建；成功返回 True，已存在/出错返回 False。"""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _touch(path):
    try:
        if os.path.exists(path):
            os.utime(path, None)
        else:
            _try_create(path)
    except OSError:
        pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


@contextlib.contextmanager
def dm_batch():
    """DM 一个发送批期间置 .dm-want（route-B 据此让位）。未启用时 no-op。"""
    if not ENABLED:
        yield
        return
    _touch(_DMWANT)
    try:
        yield
    finally:
        _remove(_DMWANT)


def dm_keepalive():
    """DM 批里每条发送前调一下，刷新 .dm-want mtime，防长批被误判 stale。未启用时 no-op。"""
    if ENABLED:
        _touch(_DMWANT)


@contextlib.contextmanager
def window_turn(role):
    """抢单抖音窗口操作权。role='dm' 直接抢；role='rb' 先让 DM(等 .dm-want 清)。未启用时 no-op。

    阻塞直到拿到锁；持有者疑似已死(mtime>STALE_SEC)则抢占清除。退出时释放。
    AKKE_GUI_LOCK=1 时拿到抖音窗口锁后再低优先级拿全机锁（见模块头），等不到抛 GuiLockTimeout。
    """
    gui = gui_lock_enabled()
    if not ENABLED and not gui:
        yield
        return
    while ENABLED:
        if role == "rb" and _fresh(_DMWANT):
            time.sleep(_POLL)
            continue                       # DM 占着/在等 → route-B 让位
        if _try_create(_LOCK):
            break                          # 抢到
        if not _fresh(_LOCK):
            _remove(_LOCK)                 # 持有者已死 → 抢占
            continue
        time.sleep(_POLL)
    try:
        if gui and not _gui_acquire_keeping_window_lock(role):
            raise GuiLockTimeout('gui_lock_timeout role=%s: 全机 GUI 锁等了 %.0fs 没拿到'
                                 '（微信侧一直在用），本条不动 GUI' % (role, _GUI.wait_sec()))
        try:
            yield
        finally:
            if gui:
                _GUI.release()
    finally:
        if ENABLED:
            _remove(_LOCK)


@contextlib.contextmanager
def gui_turn(role, wait_sec=None):
    """只拿全机 GUI 锁（低优先级），不碰抖音窗口锁。AKKE_GUI_LOCK 未设=1 时 no-op。

    给「不经 window_turn、但也动抖音窗口」的子进程外层用（poll agent 的收件箱捕获 /
    气泡捕获）。等不到抛 GuiLockTimeout —— 调用方跳过本轮。
    """
    if not gui_lock_enabled():
        yield
        return
    if not _GUI.acquire(wait_sec):
        raise GuiLockTimeout('gui_lock_timeout role=%s: 全机 GUI 锁等超时（微信侧一直在用）' % role)
    try:
        yield
    finally:
        _GUI.release()


def is_gui_lock_timeout(exc):
    """调用方判「这是等全机锁超时（没动 GUI）」用；按类名判，缺模块时的桩也能用同一写法。"""
    return type(exc).__name__ == 'GuiLockTimeout'


# 等全机锁期间手里还攥着抖音窗口锁 → 按这个间隔续 _LOCK 的 mtime，
# 否则等超过 STALE_SEC(300s) 会被同机别的抖音脚本当「持有者已死」抢走。
_KEEP_WINDOW_LOCK_SEC = 30.0


def _gui_acquire_keeping_window_lock(role):
    stop = threading.Event()
    if ENABLED:
        def _keep():
            while not stop.wait(_KEEP_WINDOW_LOCK_SEC):
                _touch(_LOCK)
        threading.Thread(target=_keep, name='akke-douyin-lock-keep', daemon=True).start()
    try:
        return _GUI.acquire()
    finally:
        stop.set()


# >>> gui-lock core（逐字拷自 Akke-AI/wecom-chat cloudpc/windows-weixin/gui_lock.py，改协议两仓一起改）
import os as _gl_os
import threading as _gl_threading
import time as _gl_time
import uuid as _gl_uuid

GUI_LOCK_ENV = "AKKE_GUI_LOCK"
GUI_LOCK_DEFAULT_DIR = r"C:\ProgramData\akke-gui"
GUI_LOCK_FILE = "gui.lock"
GUI_LOCK_WANT_PREFIX = "want."
GUI_LOCK_STALE_SEC = 120.0
GUI_LOCK_WANT_STALE_SEC = 60.0
GUI_LOCK_HEARTBEAT_SEC = 15.0
GUI_LOCK_MAX_HOLD_SEC = 900.0
GUI_LOCK_POLL_SEC = 0.5
GUI_LOCK_WECHAT_WAIT_SEC = 60.0
GUI_LOCK_DOUYIN_WAIT_SEC = 600.0


def gui_lock_enabled(environ=None) -> bool:
    """`AKKE_GUI_LOCK=1` 才生效。每次现读 env（.env 可能晚于 import 才加载）。"""
    env = _gl_os.environ if environ is None else environ
    return str(env.get(GUI_LOCK_ENV, "")).strip() == "1"


class GuiLockTimeout(RuntimeError):
    """让出锁之后在等锁上限内没能拿回来（调用方按「本轮跳过」处理）。"""


class GuiLock:
    """一个进程一个实例。`high_priority=True` = 微信侧（写 want、不让位）。"""

    def __init__(self, role, high_priority, log=None, directory=None, environ=None,
                 stale_sec=GUI_LOCK_STALE_SEC, want_stale_sec=GUI_LOCK_WANT_STALE_SEC,
                 heartbeat_sec=GUI_LOCK_HEARTBEAT_SEC, max_hold_sec=GUI_LOCK_MAX_HOLD_SEC,
                 poll_sec=GUI_LOCK_POLL_SEC, wait_sec=None):
        self.role = str(role)
        self.high = bool(high_priority)
        self._log = log
        self._environ = environ
        self._dir = directory
        self.stale_sec = float(stale_sec)
        self.want_stale_sec = float(want_stale_sec)
        self.heartbeat_sec = float(heartbeat_sec)
        self.max_hold_sec = float(max_hold_sec)
        self.poll_sec = float(poll_sec)
        self._wait_sec = wait_sec
        self._mu = _gl_threading.RLock()
        self._depth = 0
        self._token = ""
        self._acquired_at = 0.0
        self._yielded = False
        self._hb_stop = None

    # ---- 路径 / 参数 ----
    def _env(self):
        return _gl_os.environ if self._environ is None else self._environ

    def enabled(self) -> bool:
        return gui_lock_enabled(self._environ)

    def directory(self) -> str:
        return self._dir or self._env().get("AKKE_GUI_LOCK_DIR") or GUI_LOCK_DEFAULT_DIR

    def lock_path(self) -> str:
        return _gl_os.path.join(self.directory(), GUI_LOCK_FILE)

    def want_path(self) -> str:
        return _gl_os.path.join(self.directory(), GUI_LOCK_WANT_PREFIX + self.role)

    def wait_sec(self) -> float:
        raw = str(self._env().get("AKKE_GUI_LOCK_WAIT_SEC", "") or "").strip()
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
        if self._wait_sec is not None:
            return float(self._wait_sec)
        return GUI_LOCK_WECHAT_WAIT_SEC if self.high else GUI_LOCK_DOUYIN_WAIT_SEC

    @property
    def held(self) -> bool:
        return self._depth > 0 and not self._yielded

    # ---- 文件原语 ----
    def _say(self, msg: str) -> None:
        if self._log is not None:
            try:
                self._log("[gui-lock] " + msg)
            except Exception:  # noqa: BLE001 — 日志挂了不能挡锁
                pass

    def _age(self, path: str):
        try:
            return _gl_time.time() - _gl_os.path.getmtime(path)
        except OSError:
            return None

    def _fresh(self, path: str, limit: float) -> bool:
        age = self._age(path)
        return age is not None and age < limit

    def _touch(self, path: str) -> None:
        try:
            if _gl_os.path.exists(path):
                _gl_os.utime(path, None)
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"{_gl_os.getpid()} {self.role}\n")
        except OSError:
            pass

    def _read(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(200).strip()
        except OSError:
            return ""

    def _remove(self, path: str) -> None:
        try:
            _gl_os.remove(path)
        except OSError:
            pass

    def _try_create(self, token: str) -> bool:
        try:
            fd = _gl_os.open(self.lock_path(), _gl_os.O_CREAT | _gl_os.O_EXCL | _gl_os.O_WRONLY)
        except OSError:
            return False
        try:
            stamp = _gl_time.strftime("%Y-%m-%dT%H:%M:%S")
            _gl_os.write(fd, f"{_gl_os.getpid()} {self.role} {token} {stamp}".encode())
        finally:
            _gl_os.close(fd)
        return True

    def _steal_if_stale(self) -> bool:
        """持有者疑似已死 → 抢占清除。先改名成墓碑再核一次，避免两个等待者互删对方的新锁。"""
        lock = self.lock_path()
        age = self._age(lock)
        if age is None or age < self.stale_sec:
            return False
        tomb = f"{lock}.stale-{_gl_os.getpid()}-{_gl_uuid.uuid4().hex[:6]}"
        try:
            _gl_os.rename(lock, tomb)
        except OSError:
            return False
        tomb_age = self._age(tomb)
        if tomb_age is not None and tomb_age < self.stale_sec:
            # 改名那一瞬间别人刚好建了新锁：还回去（目标已被占就放弃，宁可让它按 stale 过期）
            try:
                if not _gl_os.path.exists(lock):
                    _gl_os.rename(tomb, lock)
                    return False
            except OSError:
                pass
        self._say(f"抢占 stale 锁（{age:.0f}s 未刷新 > {self.stale_sec:.0f}s）"
                  f"原持有者={self._read(tomb)!r}")
        self._remove(tomb)
        return True

    def douyin_should_yield(self) -> bool:
        """有没有新鲜的微信 want 标记（抖音侧据此不开新回合）。"""
        try:
            names = _gl_os.listdir(self.directory())
        except OSError:
            return False
        for name in names:
            if name.startswith(GUI_LOCK_WANT_PREFIX) and self._fresh(
                    _gl_os.path.join(self.directory(), name), self.want_stale_sec):
                return True
        return False

    # ---- 心跳 ----
    def _heartbeat_loop(self, stop) -> None:
        while not stop.wait(self.heartbeat_sec):
            with self._mu:
                if self._depth <= 0:
                    return
                if _gl_time.time() - self._acquired_at >= self.max_hold_sec:
                    continue   # 占太久了：不再刷，让它按 stale 被抢（卡死保护）
                if self.high:
                    self._touch(self.want_path())
                if (not self._yielded and self._token
                        and self._token in self._read(self.lock_path())):
                    try:
                        _gl_os.utime(self.lock_path(), None)
                    except OSError:
                        pass

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        stop = _gl_threading.Event()
        self._hb_stop = stop
        t = _gl_threading.Thread(target=self._heartbeat_loop, args=(stop,),
                                 name=f"akke-gui-lock-{self.role}", daemon=True)
        t.start()

    def _stop_heartbeat(self) -> None:
        if self._hb_stop is not None:
            self._hb_stop.set()
            self._hb_stop = None

    # ---- 对外 ----
    def _wait_for_lock(self, wait_sec: float) -> bool:
        """真正抢锁（不管深度）。拿到返回 True（已记 token / 时间），超时返回 False。"""
        try:
            _gl_os.makedirs(self.directory(), exist_ok=True)
        except OSError as e:
            self._say(f"建目录失败 {self.directory()}: {e} —— 本轮不动 GUI")
            return False
        start = _gl_time.time()
        deadline = start + max(0.0, wait_sec)
        announced = False
        while True:
            if self.high:
                self._touch(self.want_path())
            yielding = (not self.high) and self.douyin_should_yield()
            if not yielding:
                token = _gl_uuid.uuid4().hex[:12]
                if self._try_create(token):
                    self._token = token
                    self._acquired_at = _gl_time.time()
                    if announced:
                        self._say(f"{self.role} 等了 {self._acquired_at - start:.1f}s 拿到锁")
                    return True
                if self._steal_if_stale():
                    continue
            why = ("微信侧有活（want 新鲜），抖音让位" if yielding
                   else f"持有者={self._read(self.lock_path())!r}")
            if _gl_time.time() >= deadline:
                self._say(f"{self.role} 等锁 {wait_sec:.0f}s 超时，跳过本轮（{why}）")
                return False
            if not announced:
                announced = True
                self._say(f"{self.role} 等锁中（上限 {wait_sec:.0f}s；{why}）")
            _gl_time.sleep(self.poll_sec)

    def acquire(self, wait_sec=None) -> bool:
        """拿锁。未启用 = 恒 True 且不碰文件。同进程可重入（深度计数）。"""
        if not self.enabled():
            return True
        with self._mu:
            if self._depth > 0:
                self._depth += 1
                return True
        ok = self._wait_for_lock(self.wait_sec() if wait_sec is None else float(wait_sec))
        if not ok:
            return False
        with self._mu:
            self._depth = 1
            self._yielded = False
            self._start_heartbeat()
        return True

    def _drop_lock_file(self) -> None:
        if self._token and self._token in self._read(self.lock_path()):
            self._remove(self.lock_path())
        self._token = ""

    def release(self) -> None:
        """放一层。最外层放完才真删锁文件（只删自己 token 的那份）和自己的 want。"""
        with self._mu:
            if self._depth <= 0:
                return
            self._depth -= 1
            if self._depth > 0:
                return
            self._stop_heartbeat()
            if not self._yielded:
                self._drop_lock_file()
            self._yielded = False
            if self.high:
                self._remove(self.want_path())

    def release_all(self) -> None:
        """兜底：不管嵌套多深全部放掉（主循环每轮末尾调，防某条路径漏了 release）。"""
        with self._mu:
            if self._depth > 1:
                self._depth = 1
        self.release()

    def yield_turn(self, tag: str = ""):
        """`with lock.yield_turn("post"):` —— 等网络（LLM）期间把锁让出去，结束时再拿回来。

        微信侧让出期间 want 仍由心跳刷着 → 抖音不会趁机进来，只有另一家微信能用这段空档。
        没持锁 / 未启用时是 no-op。拿不回来抛 GuiLockTimeout（调用方按跳过本轮处理）。
        """
        return _GuiLockYield(self, tag)


class _GuiLockYield:
    def __init__(self, lock, tag):
        self.lock, self.tag, self.active = lock, tag, False

    def __enter__(self):
        lk = self.lock
        if not lk.enabled():
            return self
        with lk._mu:
            if lk._depth <= 0 or lk._yielded:
                return self
            lk._drop_lock_file()
            lk._yielded = True
            self.active = True
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if not self.active:
            return False
        lk = self.lock
        if lk._wait_for_lock(lk.wait_sec()):
            with lk._mu:
                lk._yielded = False
            return False
        # 拿不回来：整体视为已放锁，调用方跳过本轮
        with lk._mu:
            lk._depth = 1
        lk.release()
        if exc_type is not None:
            return False   # 原异常优先往上抛
        raise GuiLockTimeout(f"gui-lock 让出后 {lk.wait_sec():.0f}s 内没拿回来（{self.tag}）")
# <<< gui-lock core


_GUI = GuiLock("douyin", high_priority=False, log=lambda m: print('  ' + m, flush=True))
