# -*- coding: utf-8 -*-
"""
视频动态壁纸（Windows 10/11）—— 低功耗版

行为：
  亮屏 / 解锁 / 登录后 -> 播放 burst_seconds 秒（默认 8s）-> 冻结在最后一帧
  屏幕关闭、锁屏 -> 立刻暂停解码
  其余时间完全不解码（GPU 解码引擎归零，几乎零开销）

原理：
  1. 向 Progman 发送 0x052C 让系统在其下分出子 WorkerW（壁纸层）
  2. 自建窗口 SetParent 进该 WorkerW，鼠标事件穿透
  3. mpv --wid 渲染进该窗口，通过 --input-ipc-server 命名管道控制 pause
  4. 用 RegisterPowerSettingNotification(DISPLAY_STATE) + WTS 会话通知做事件驱动

用法:
  pythonw wallpaper_player.py
  python  wallpaper_player.py --video xxx.mov --burst 10
"""
import os
import sys
import json
import time
import ctypes
import shutil
import subprocess
from ctypes import wintypes

if getattr(sys, "frozen", False):
    # 打包成 exe 后，配置/日志/mpv/视频都以 exe 所在目录为准
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
STATUS = os.path.join(HERE, "status.json")
PIPE_NAME = "aerial_wallpaper_mpv"
# 控制面板 → 播放器的命名事件（毫秒级指令通道，不依赖进程名/命令行匹配）
EVT_STOP = "Global\\AerialWallpaper_ctl_stop"
EVT_PLAY = "Global\\AerialWallpaper_ctl_play"

DEFAULT_BURST = 8.0          # 亮屏后播放秒数
DEFAULT_REFRESH_MIN = 0.0    # >0 则每 N 分钟轻微"动一下"，0=关闭
DEFAULT_IDLE_TRIGGER = 20.0  # 空闲超过 N 秒后回来 -> 播一次


# ---------------------------------------------------------------- 配置

def load_config():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("config.json 解析失败，忽略:", e, file=sys.stderr)
    return {}


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def find_video(cfg):
    v = arg("--video")
    if v:
        return v
    if cfg.get("video"):
        return cfg["video"]
    for name in sorted(os.listdir(HERE)):
        if name.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v")):
            return os.path.join(HERE, name)
    return os.path.join(HERE, "video.mp4")


def find_mpv(cfg):
    m = arg("--mpv")
    if m:
        return m
    if cfg.get("mpv") and os.path.exists(cfg["mpv"]):
        return cfg["mpv"]
    local = os.path.join(HERE, "mpv", "mpv.exe")
    if os.path.exists(local):
        return local
    for p in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Lively Wallpaper\Plugins\Mpv\mpv.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Lively Wallpaper\Plugins\Mpv\mpv.exe"),
    ):
        if os.path.exists(p):
            return p
    return shutil.which("mpv") or ""


# ---------------------------------------------------------------- Win32

u = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
powr = ctypes.WinDLL("powrprof", use_last_error=True)
wts = ctypes.WinDLL("wtsapi32", use_last_error=True)

try:
    u.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
except Exception:
    try:
        u.SetProcessDPIAware()
    except Exception:
        pass

H = wintypes.HWND
u.FindWindowW.restype = H
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.FindWindowExW.restype = H
u.FindWindowExW.argtypes = [H, H, wintypes.LPCWSTR, wintypes.LPCWSTR]
u.DefWindowProcW.restype = ctypes.c_long
u.DefWindowProcW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.SetParent.restype = H
u.SetParent.argtypes = [H, H]
u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.ShowWindow.argtypes = [H, ctypes.c_int]
u.GetWindowLongW.restype = ctypes.c_long
u.GetWindowLongW.argtypes = [H, ctypes.c_int]
u.SetWindowLongW.restype = ctypes.c_long
u.SetWindowLongW.argtypes = [H, ctypes.c_int, ctypes.c_long]
u.SendMessageTimeoutW.restype = ctypes.c_size_t
u.SendMessageTimeoutW.argtypes = [H, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t,
                                  wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
u.IsWindow.argtypes = [H]
u.IsWindow.restype = wintypes.BOOL
u.MsgWaitForMultipleObjects.restype = wintypes.DWORD
u.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                        wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.SetPriorityClass.restype = wintypes.BOOL
k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.CreateEventW.restype = wintypes.HANDLE
k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k32.SetEvent.argtypes = [wintypes.HANDLE]
k32.SetEvent.restype = wintypes.BOOL
k32.OpenEventW.restype = wintypes.HANDLE
k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k32.ResetEvent.argtypes = [wintypes.HANDLE]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD),
                ("Data", ctypes.c_ubyte * 16)]


GUID_CONSOLE_DISPLAY_STATE = GUID(
    0x6FE69556, 0x704A, 0x47A0,
    (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))

powr.PowerSettingRegisterNotification.restype = wintypes.HANDLE
u.RegisterPowerSettingNotification.restype = wintypes.HANDLE
u.RegisterPowerSettingNotification.argtypes = [H, ctypes.POINTER(GUID), wintypes.DWORD]
wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
wts.WTSRegisterSessionNotification.argtypes = [H, wintypes.DWORD]
u.GetLastInputInfo.restype = wintypes.BOOL
k32.GetTickCount.restype = wintypes.DWORD


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    """距最后一次键鼠输入的秒数（用于"用户回来"兜底判定）"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not u.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    now = k32.GetTickCount()
    return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def log(msg):
    try:
        with open(os.path.join(HERE, "player.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
u.CreateWindowExW.restype = H
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              H, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SW_SHOW = 5
SW_HIDE = 0
SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
GWL_STYLE = -16
GWL_EXSTYLE = -20
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
WM_WTSSESSION_CHANGE = 0x02B1
HTTRANSPARENT = -1

WTS_CONSOLE_CONNECT = 0x1
WTS_SESSION_UNLOCK = 0x8

QS_ALLINPUT = 0x04FF
WAIT_OBJECT_0 = 0
PROCESS_SYNCHRONIZE = 0x00100000
PROCESS_SET_INFORMATION = 0x0200
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
DEVICE_NOTIFY_WINDOW_HANDLE = 0x0
NOTIFY_FOR_THIS_SESSION = 0x0

CLASS_NAME = "AerialWallpaperHost"

# 事件状态：wndproc 写，主循环读（同一线程）
STATE = {"burst_until": 0.0, "force_pause": False, "log": []}


def trigger_burst(seconds, why=""):
    STATE["burst_until"] = time.time() + seconds
    STATE["force_pause"] = False
    if why:
        STATE["log"].append(why)


def _wndproc(hwnd, msg, wp, lp):
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT                     # 鼠标事件全部穿透到桌面图标
    if msg == WM_ERASEBKGND:
        return 1                                 # 不擦背景，避免闪底色
    if msg == WM_POWERBROADCAST and wp == PBT_POWERSETTINGCHANGE:
        try:
            pbs = ctypes.cast(lp, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
            state = pbs.Data[0]                  # 0=off 1=on 2=dimmed
            if state == 0:
                STATE["burst_until"] = 0.0
                STATE["force_pause"] = True
                STATE["log"].append("display-off")
            elif state == 1:
                trigger_burst(BURST, "display-on")
        except Exception:
            pass
    elif msg == WM_WTSSESSION_CHANGE:
        if wp in (WTS_SESSION_UNLOCK, WTS_CONSOLE_CONNECT):
            trigger_burst(BURST, "unlock" if wp == WTS_SESSION_UNLOCK else "console-connect")
    return u.DefWindowProcW(hwnd, msg, wp, lp)


_proc_ref = WNDPROC(_wndproc)  # 防 GC


def screen_size():
    """GetDeviceCaps 取物理像素，不受 DPI 虚拟化影响"""
    g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = [H]
    u.ReleaseDC.argtypes = [H, wintypes.HDC]
    g32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    dc = u.GetDC(0)
    w = g32.GetDeviceCaps(dc, 118)   # HORZRES
    h = g32.GetDeviceCaps(dc, 117)   # VERTRES
    u.ReleaseDC(0, dc)
    return w, h


LAYER_HWND = None


def _find_workerw(progman):
    """在 Progman 下找一个"纯"WorkerW（不含 SHELLDLL_DefView 图标层的那个）"""
    found = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, H, wintypes.LPARAM)

    def on_child(h, lp):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value == "WorkerW":
            found.append(h)
        return True

    ref = CB(on_child)
    u.EnumChildWindows(progman, ctypes.cast(ref, ctypes.c_void_p), 0)

    for h in found:
        kids = []

        def on_kid(ch, lp, _out=kids):
            _out.append(ch)
            return True

        ref2 = CB(on_kid)
        u.EnumChildWindows(h, ctypes.cast(ref2, ctypes.c_void_p), 0)
        has_defview = False
        for k in kids:
            c = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(k, c, 64)
            if c.value == "SHELLDLL_DefView":
                has_defview = True
                break
        if not has_defview:
            return h
    return found[0] if found else None


def get_wallpaper_layer():
    """取桌面层（WorkerW）。

    关键：0x052C 每调用一次就会让 Progman **新建**一个 WorkerW 并让旧的那个失效，
    挂在旧层下的窗口会被连带销毁（表现为壁纸窗口消失、mpv 变成独立播放窗口）。
    所以这里优先复用已存在的 WorkerW，只有确实找不到时才触发一次 0x052C。
    """
    global LAYER_HWND
    progman = u.FindWindowW("Progman", None)
    if not progman:
        return None
    if LAYER_HWND and u.IsWindow(LAYER_HWND):
        return LAYER_HWND
    w = _find_workerw(progman)
    if w:
        LAYER_HWND = w
        return w
    res = ctypes.c_size_t()
    for wp, lp in ((0xD, 0x1), (0xD, 0x0), (0, 0)):
        u.SendMessageTimeoutW(progman, 0x052C, wp, lp, 0, 1000, ctypes.byref(res))
    time.sleep(0.35)
    LAYER_HWND = _find_workerw(progman) or progman
    return LAYER_HWND


def make_wallpaper_window(show=True):
    layer = get_wallpaper_layer()
    if not layer:
        return None
    w, h = screen_size()
    hinst = k32.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.lpfnWndProc = _proc_ref
    wc.hInstance = hinst
    wc.lpszClassName = CLASS_NAME
    u.RegisterClassW(ctypes.byref(wc))
    hwnd = u.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
                             CLASS_NAME, "AerialWallpaper", WS_POPUP,
                             0, 0, w, h, None, None, hinst, None)
    if not hwnd:
        return None
    u.SetParent(hwnd, layer)
    style = u.GetWindowLongW(hwnd, GWL_STYLE)
    u.SetWindowLongW(hwnd, GWL_STYLE,
                     (style & ~WS_POPUP) | WS_CHILD | WS_CLIPSIBLINGS)
    if show:
        u.SetWindowPos(hwnd, None, 0, 0, w, h,
                       SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        u.ShowWindow(hwnd, SW_SHOW)
    else:
        u.SetWindowPos(hwnd, None, 0, 0, w, h, SWP_NOACTIVATE | SWP_FRAMECHANGED)
    ensure_notify_window()
    return hwnd


def show_wallpaper_window(hwnd):
    u.ShowWindow(hwnd, SW_SHOW)
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                   SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)


def hide_wallpaper_window(hwnd):
    u.ShowWindow(hwnd, SW_HIDE)


def poster_path(video):
    """首帧海报：与视频同目录的 <名字>.poster.jpg"""
    return os.path.splitext(video)[0] + ".poster.jpg"


def set_static_wallpaper(path):
    """把系统静态壁纸换成指定图片，用作启动过渡时的兜底画面"""
    try:
        u.SystemParametersInfoW.restype = wintypes.BOOL
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                            wintypes.LPCWSTR, wintypes.UINT]
        return bool(u.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path,
                                            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))
    except Exception as e:
        log(f"set_static_wallpaper failed: {e}")
        return False


def get_static_wallpaper():
    """读取当前系统静态壁纸路径（用于记住用户原图，停止时恢复）"""
    try:
        u.SystemParametersInfoW.restype = wintypes.BOOL
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                            wintypes.LPVOID, wintypes.UINT]
        buf = ctypes.create_unicode_buffer(512)
        if u.SystemParametersInfoW(0x0015, 512, buf, 0):   # SPI_GETDESKWALLPAPER
            return buf.value
    except Exception:
        pass
    return ""


def wait_mpv_ready(hwnd, timeout=2.5, settle=0.25):
    """等 mpv 在宿主窗口内建好渲染子窗口（说明首帧即将/已经呈现）"""
    kids = []

    def cb(child, lp):
        kids.append(child)
        return True

    proc = u.EnumChildWindows
    proc.argtypes = [H, ctypes.c_void_p, wintypes.LPARAM]
    cb_ref = ctypes.WINFUNCTYPE(wintypes.BOOL, H, wintypes.LPARAM)(cb)
    t0 = time.time()
    while time.time() - t0 < timeout:
        kids.clear()
        proc(hwnd, ctypes.cast(cb_ref, ctypes.c_void_p), 0)
        if kids:
            time.sleep(settle)
            return True
        time.sleep(0.05)
    return False


NOTIFY_HWND = None


def ensure_notify_window():
    """独立的隐藏顶层窗口：壁纸窗口是子窗口，收不到系统电源广播，
    必须由一个顶层窗口代收（电源设置变更 / 锁屏解锁通知）。"""
    global NOTIFY_HWND
    if NOTIFY_HWND and u.IsWindow(NOTIFY_HWND):
        return NOTIFY_HWND
    hinst = k32.GetModuleHandleW(None)
    h = u.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, CLASS_NAME,
                          "AerialWallpaperNotifier", WS_POPUP,
                          -32000, -32000, 0, 0, None, None, hinst, None)
    if not h:
        log("notify window: CREATE FAILED")
        return None
    hp = u.RegisterPowerSettingNotification(h, ctypes.byref(GUID_CONSOLE_DISPLAY_STATE),
                                            DEVICE_NOTIFY_WINDOW_HANDLE)
    ok = wts.WTSRegisterSessionNotification(h, NOTIFY_FOR_THIS_SESSION)
    NOTIFY_HWND = h
    log(f"notify hwnd={h} power={bool(hp)} wts={bool(ok)}")
    return h


# ---------------------------------------------------------------- mpv IPC

def ipc_connect(timeout=8.0):
    path = "\\\\.\\pipe\\" + PIPE_NAME
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return open(path, "r+b", buffering=0)
        except OSError:
            time.sleep(0.2)
    return None


def ipc_send(pipe, obj):
    try:
        pipe.write((json.dumps(obj) + "\n").encode("utf-8"))
        return True
    except Exception:
        return False


def ipc_pause(pipe, paused):
    return ipc_send(pipe, {"command": ["set_property", "pause", bool(paused)]})


# ---------------------------------------------------------------- 单实例

def acquire_single_instance():
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    h = k32.CreateMutexW(None, True, "Global\\AerialWallpaperPlayer")
    if not h or ctypes.get_last_error() == 183:
        return None
    return h


def kill_stray_mpv():
    """启动时清理上次异常退出遗留的 mpv（父窗口已没、会变成屏幕上的独立播放窗口）"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='mpv.exe'\" | "
             "Where-Object { $_.CommandLine -like '*aerial_wallpaper_mpv*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"],
            capture_output=True, text=True, timeout=8)
        killed = [x for x in (r.stdout or "").split() if x.strip().isdigit()]
        if killed:
            log("killed stray mpv: " + ",".join(killed))
        return killed
    except Exception as e:
        log(f"kill_stray_mpv failed: {e}")
        return []


def main():
    global BURST
    if not acquire_single_instance():
        print("已有实例在运行，退出。", file=sys.stderr)
        return
    kill_stray_mpv()

    cfg = load_config()
    BURST = float(arg("--burst", cfg.get("burst_seconds", DEFAULT_BURST)))
    refresh_min = float(cfg.get("refresh_minutes", DEFAULT_REFRESH_MIN))
    idle_trig = float(cfg.get("idle_trigger_seconds", DEFAULT_IDLE_TRIGGER))
    prev_idle = None

    video = find_video(cfg)
    mpv = find_mpv(cfg)
    if not mpv or not os.path.exists(mpv):
        sys.exit("找不到 mpv.exe，请用 --mpv 指定，或放到本目录 mpv/ 下")
    if not os.path.exists(video):
        sys.exit("找不到视频: " + video)

    # 过渡兜底：先把系统静态壁纸设成首帧海报，再等 mpv 渲染出画面才显示视频窗口。
    # 这样启动/换片过程中用户看到的画面不变，不会"先露原壁纸再闪一下"。
    # 只在第一次记住用户原图，供停止壁纸时恢复。
    if not cfg.get("original_wallpaper"):
        orig = get_static_wallpaper()
        if orig and not orig.endswith(".poster.jpg"):
            try:
                cfg["original_wallpaper"] = orig
                with open(CONFIG, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                log("original wallpaper remembered: " + orig)
            except Exception:
                pass
    poster = poster_path(video)
    if os.path.exists(poster):
        set_static_wallpaper(poster)

    hwnd = make_wallpaper_window(show=False)
    if not hwnd:
        sys.exit("创建壁纸窗口失败（找不到 Progman？）")

    proc = hproc = pipe = None
    paused = None

    # 控制面板的指令事件（自动复位）
    ctl_stop = k32.CreateEventW(None, False, False, EVT_STOP)
    ctl_play = k32.CreateEventW(None, False, False, EVT_PLAY)
    for h in (ctl_stop, ctl_play):
        if h:
            k32.ResetEvent(h)

    def reveal():
        """mpv 渲染就绪后再显示壁纸窗口，避免露黑场"""
        if not wait_mpv_ready(hwnd):
            log("mpv 就绪等待超时，直接显示")
        show_wallpaper_window(hwnd)

    def capture_poster():
        """用 mpv 截当前帧做下次启动的海报（已存在则跳过）"""
        nonlocal poster
        poster = poster_path(video)
        if os.path.exists(poster) or not pipe:
            return
        if ipc_send(pipe, {"command": ["screenshot-to-file", poster, "video"]}):
            log("poster saved: " + poster)

    def shutdown(reason="panel"):
        """优雅停止：收 mpv、销毁窗口、恢复用户原壁纸（面板「停止壁纸」走这里）"""
        log("shutdown requested by " + reason)
        try:
            if pipe:
                ipc_pause(pipe, True)
                pipe.close()
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            pass
        orig = load_config().get("original_wallpaper")
        if orig and os.path.exists(orig):
            set_static_wallpaper(orig)
        try:
            if u.IsWindow(hwnd):
                u.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            if os.path.exists(STATUS):
                os.remove(STATUS)
        except Exception:
            pass

    def launch():
        nonlocal proc, hproc
        proc = subprocess.Popen(
            [mpv, f"--wid={hwnd}", "--loop=inf", "--no-audio", "--really-quiet",
             "--no-osd-bar", "--osd-level=0", "--hwdec=auto-safe",
             "--video-unscaled=no", "--keepaspect=yes", "--panscan=0.0",
             "--no-input-default-bindings", "--no-osc", "--border=no",
             f"--input-ipc-server={PIPE_NAME}",
             "--keep-open=yes", video],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        hproc = k32.OpenProcess(PROCESS_SYNCHRONIZE, False, proc.pid)
        hs = k32.OpenProcess(PROCESS_SET_INFORMATION, False, proc.pid)
        if hs:
            k32.SetPriorityClass(hs, BELOW_NORMAL_PRIORITY_CLASS)
            k32.CloseHandle(hs)

    def restart(delay=0.0):
        nonlocal pipe, paused
        if delay:
            time.sleep(delay)
        # 换片时同样先藏窗口 + 换成新片首帧海报，避免黑场闪一下
        hide_wallpaper_window(hwnd)
        p2 = poster_path(video)
        if os.path.exists(p2):
            set_static_wallpaper(p2)
        if hproc:
            try:
                k32.CloseHandle(hproc)
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass
        try:
            if pipe:
                pipe.close()
        except Exception:
            pass
        pipe = None
        paused = None
        launch()

    def load_in_place(newv):
        """就地换片：让现有 mpv 直接 loadfile，进程和渲染窗口都不重建。

        这是"换片时冒出播放窗口"的根治办法——不新建进程就不会有新的顶层窗口。
        """
        nonlocal pipe
        if pipe is None:
            pipe = ipc_connect(timeout=3)
        if not pipe:
            return False
        if not ipc_send(pipe, {"command": ["loadfile", newv, "replace"]}):
            return False
        ipc_send(pipe, {"command": ["set_property", "loop-file", "inf"]})
        if paused:
            ipc_pause(pipe, True)
        return True

    def rebuild_host():
        """宿主窗口失效（桌面层被重建）时整体重建：先收掉旧 mpv，再建窗口、起新 mpv"""
        nonlocal hwnd, proc, hproc, pipe, paused
        if hproc:
            try:
                k32.CloseHandle(hproc)
            except Exception:
                pass
            hproc = None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
            proc = None
        try:
            if pipe:
                pipe.close()
        except Exception:
            pass
        pipe = None
        paused = None
        newh = make_wallpaper_window(show=False)
        if not newh:
            return False
        hwnd = newh
        launch()
        pipe = ipc_connect()
        reveal()
        capture_poster()
        trigger_burst(BURST, "host-rebuilt")
        return True

    launch()
    pipe = ipc_connect()
    reveal()
    capture_poster()
    trigger_burst(BURST, "start")
    print(f"host={hwnd} mpv={proc.pid} video={video} burst={BURST:g}s", flush=True)

    cli_override = "--video" in sys.argv
    cfg_mtime = os.path.getmtime(CONFIG) if os.path.exists(CONFIG) else 0
    last_nudge = (load_config().get("nudge") or 0)
    msg = wintypes.MSG()
    next_cfg = next_win = time.time() + 1
    next_refresh = time.time() + refresh_min * 60 if refresh_min > 0 else float("inf")
    seen_log = 0
    pending_poster = False

    def write_status(reason=""):
        try:
            tmp = STATUS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"paused": paused, "reason": reason, "ts": round(time.time(), 1),
                           "burst_until": round(STATE["burst_until"], 1),
                           "video": os.path.basename(video), "pid": os.getpid()}, f)
            os.replace(tmp, STATUS)
        except Exception:
            pass

    write_status("start")

    while True:
        # 等待：mpv 进程退出 / 面板「停止」/ 面板「立即动一下」/ 消息
        items = []
        if hproc:
            items.append(("proc", hproc))
        if ctl_stop:
            items.append(("stop", ctl_stop))
        if ctl_play:
            items.append(("play", ctl_play))
        arr = (wintypes.HANDLE * max(1, len(items)))(*[h for _, h in items]) if items else None
        r = u.MsgWaitForMultipleObjects(len(items), arr, False, 200, QS_ALLINPUT)
        fired = items[r][0] if (arr is not None and 0 <= r < len(items)) else None

        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

        if fired == "stop":
            shutdown("panel")
            return
        if fired == "play":
            trigger_burst(BURST, "panel")
            write_status("panel")

        now = time.time()

        # 事件日志（wndproc 写的）
        while seen_log < len(STATE["log"]):
            log("event: " + STATE["log"][seen_log])
            print(f"event: {STATE['log'][seen_log]}", flush=True)
            seen_log += 1

        # 兜底触发：空闲很久后用户回来（= 亮屏/解锁/唤醒的实际表现）
        idle = idle_seconds()
        if prev_idle is not None and prev_idle >= idle_trig and idle < 1.5:
            trigger_burst(BURST, "user-return")
        prev_idle = idle

        if fired == "proc" or proc.poll() is not None:
            restart(1.0)
            pipe = ipc_connect()
            reveal()
            capture_poster()
            trigger_burst(BURST, "mpv-restart")
            continue

        # 周期性轻微"动一下"（默认关闭）
        if now >= next_refresh:
            next_refresh = now + refresh_min * 60
            trigger_burst(min(BURST, 6.0), "refresh")

        # 暂停/恢复状态机
        want_paused = STATE["force_pause"] or (now >= STATE["burst_until"])
        if want_paused != paused:
            if pipe is None:
                pipe = ipc_connect(timeout=2)
            if pipe and ipc_pause(pipe, want_paused):
                paused = want_paused
                write_status("state")
                log("paused" if want_paused else "playing")
                print(("paused" if want_paused else "playing"), flush=True)
                # 换片后等画面稳定再补截海报（在冻结时做，不阻塞消息泵）
                if paused and pending_poster:
                    pending_poster = False
                    capture_poster()

        # 配置热更新（每秒）：换片 / 手动 nudget 触发一次播放
        if not cli_override and now >= next_cfg:
            next_cfg = now + 1
            if os.path.exists(CONFIG):
                try:
                    m = os.path.getmtime(CONFIG)
                    if m != cfg_mtime:
                        cfg_mtime = m
                        cfgn = load_config()
                        try:                       # 面板「保存」改的秒数立即生效
                            nb = float(cfgn.get("burst_seconds", BURST))
                            if nb > 0 and nb != BURST:
                                BURST = nb
                                log(f"burst -> {BURST:g}s")
                        except Exception:
                            pass
                        nd = cfgn.get("nudge") or 0
                        if nd and nd != last_nudge:
                            last_nudge = nd
                            trigger_burst(BURST, "nudge")
                            print("nudge -> play", flush=True)
                        newv = find_video(cfgn)
                        if newv != video and os.path.exists(newv):
                            video = newv
                            if load_in_place(video):
                                log(f"switched in place -> {os.path.basename(video)}")
                                print(f"switched (in place) -> {video}", flush=True)
                                if not os.path.exists(poster_path(video)):
                                    pending_poster = True
                            else:
                                restart()
                                pipe = ipc_connect()
                                reveal()
                                capture_poster()
                                print(f"switched (restart) -> {video}", flush=True)
                            trigger_burst(BURST, "switch")
                except Exception:
                    pass

        # 壁纸层重建（Explorer 重启 / 桌面层被系统换掉）
        if now >= next_win:
            next_win = now + 2
            if not u.IsWindow(hwnd):
                log("host window gone, rebuilding")
                rebuild_host()


if __name__ == "__main__":
    main()
