# -*- coding: utf-8 -*-
"""
动态壁纸 · 应用外壳（Win32 原生界面，无第三方依赖）

双击 exe            -> 控制面板
exe --run           -> 运行壁纸进程（无界面，供开机自启使用）
exe --install / --uninstall / --stop / --status
"""
import os
import sys
import json
import time
import ctypes
import threading
import subprocess
from ctypes import wintypes

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG = os.path.join(APP_DIR, "config.json")
STATUS = os.path.join(APP_DIR, "status.json")
TASK = "视频动态壁纸"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
MUTEX = "Global\\AerialWallpaperPlayer"
EVT_STOP = "Global\\AerialWallpaper_ctl_stop"
EVT_PLAY = "Global\\AerialWallpaper_ctl_play"

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


# ---------------------------------------------------------------- 基础工具

def ps(cmd, timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"ERR {e}"


def load_cfg():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def read_status():
    try:
        with open(STATUS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_running():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p
    k32.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    h = k32.OpenMutexW(0x00100000, False, MUTEX)
    if h:
        k32.CloseHandle(ctypes.c_void_p(h))
        return True
    return False


def signal_player(event_name, wait=2.0):
    """给播放器发指令（命名事件）。返回 True 表示播放器确实收到了"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    h = k32.OpenEventW(0x0002, False, event_name)   # EVENT_MODIFY_STATE
    if not h:
        return False
    ok = bool(k32.SetEvent(ctypes.c_void_p(h)))
    k32.CloseHandle(ctypes.c_void_p(h))
    return ok


def wait_stopped(timeout=2.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not is_running():
            return True
        time.sleep(0.1)
    return not is_running()


def start_player():
    if is_running():
        return True
    if FROZEN:
        cmd = [sys.executable, "--run"]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        cmd = [exe, os.path.join(APP_DIR, "wallpaper_player.py")]
    subprocess.Popen(cmd, cwd=APP_DIR, close_fds=True,
                     creationflags=0x00000008 | 0x08000000)
    return True


def freeze_to_video():
    """把桌面定格成当前视频的静止画面（停止壁纸时用）"""
    v = current_video()
    if not v:
        return False
    base = os.path.splitext(v)[0]
    for c in (base + ".stopframe.jpg", base + ".poster.jpg"):
        if os.path.exists(c):
            u32 = ctypes.windll.user32
            u32.SystemParametersInfoW(20, 0, c, 0x01 | 0x02)
            return True
    return False


def restore_original():
    orig = load_cfg().get("original_wallpaper")
    if orig and os.path.exists(orig):
        ctypes.windll.user32.SystemParametersInfoW(20, 0, orig, 0x01 | 0x02)
        return True
    return False


def stop_player():
    """先走事件（毫秒级、跨启动方式都有效），超时才退回强杀"""
    stopped = False
    if signal_player(EVT_STOP):
        stopped = wait_stopped(3.0)
    if not stopped:
        # 兜底：播放器没起来事件（旧版本）或卡住，按命令行强杀，两种启动方式都覆盖
        ps("Get-Process mpv -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() }; "
           "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
           "($_.CommandLine -like '*wallpaper_player.py*' -or "
           " ($_.Name -eq 'AerialWallpaper.exe' -and $_.CommandLine -like '*--run*')) } | "
           "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        wait_stopped(2.0)
        # 强杀时播放器来不及自己定格，这里补一张
        if not freeze_to_video():
            restore_original()
    return not is_running()


def task_installed():
    return "YES" in ps(f"if (Get-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue) "
                       "{ 'YES' } else { 'NO' }")


def _task_cmdlines():
    if FROZEN:
        return sys.executable, "--run"
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return exe, f'"{os.path.join(APP_DIR, "wallpaper_player.py")}"'


def install_task():
    exe, args = _task_cmdlines()
    user = f"{os.environ.get('USERDOMAIN')}\\{os.environ.get('USERNAME')}"
    cmd = f"""
$ErrorActionPreference='Stop'
$act = New-ScheduledTaskAction -Execute '{exe}' -Argument '{args}' -WorkingDirectory '{APP_DIR}'
$trg = New-ScheduledTaskTrigger -AtLogOn -User '{user}'
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$prn = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{TASK}' -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
'ok'
"""
    return "ok" in ps(cmd)


def uninstall_task():
    ps(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false -ErrorAction SilentlyContinue; 'ok'")


def videos():
    out = []
    for d in (APP_DIR, os.path.join(os.path.expanduser("~"), "Videos")):
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.lower().endswith(VIDEO_EXT):
                    out.append(os.path.join(d, n))
    return out


def current_video():
    cfg = load_cfg()
    v = cfg.get("video")
    if not v:
        vs = videos()
        v = vs[0] if vs else ""
    return v


# ---------------------------------------------------------------- Win32 UI

u = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

H = wintypes.HWND
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u.DefWindowProcW.restype = ctypes.c_long
u.DefWindowProcW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.CreateWindowExW.restype = H
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              H, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.LPVOID]
u.SetWindowTextW.argtypes = [H, wintypes.LPCWSTR]
u.GetClientRect.argtypes = [H, ctypes.c_void_p]
u.InvalidateRect.argtypes = [H, ctypes.c_void_p, wintypes.BOOL]
g32.CreateSolidBrush.restype = wintypes.HBRUSH
g32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
g32.DeleteObject.restype = wintypes.BOOL
g32.CreateFontW.restype = wintypes.HFONT
g32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wintypes.LPCWSTR]
u.FillRect.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.HBRUSH]
u.SendMessageW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.SendMessageW.restype = ctypes.c_ssize_t     # STM_SETIMAGE 返回旧 HBITMAP（64 位句柄）
g32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
g32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
g32.SetBkColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
# 64 位句柄安全：所有可能传入大句柄值的函数必须显式声明参数类型，
# 否则 ctypes 默认按 c_int 传参，句柄高位不为 0 时抛 OverflowError
u.ReleaseDC.restype = wintypes.BOOL
u.ReleaseDC.argtypes = [H, wintypes.HDC]
u.SetTimer.restype = ctypes.c_size_t
u.SetTimer.argtypes = [H, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
u.KillTimer.argtypes = [H, ctypes.c_size_t]
u.GetWindowTextLengthW.restype = ctypes.c_int
u.GetWindowTextLengthW.argtypes = [H]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
u.DestroyWindow.argtypes = [H]
u.PostQuitMessage.argtypes = [ctypes.c_int]
u.ShowWindow.restype = wintypes.BOOL
u.ShowWindow.argtypes = [H, ctypes.c_int]
u.SetForegroundWindow.restype = wintypes.BOOL
u.SetForegroundWindow.argtypes = [H]
u.SetWindowPos.restype = wintypes.BOOL
u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.FindWindowW.restype = H
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.GetSystemMetrics.restype = ctypes.c_int
u.GetSystemMetrics.argtypes = [ctypes.c_int]
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
u.GetMessageW.restype = wintypes.BOOL
u.GetMessageW.argtypes = [ctypes.c_void_p, H, wintypes.UINT, wintypes.UINT]
u.TranslateMessage.argtypes = [ctypes.c_void_p]
u.DispatchMessageW.restype = ctypes.c_long
u.DispatchMessageW.argtypes = [ctypes.c_void_p]

WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_ERASEBKGND = 0x0014
WM_CTLCOLORSTATIC = 0x0138
WM_CTLCOLORBTN = 0x0135
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_BORDER = 0x00800000
BS_AUTOCHECKBOX = 0x00000003
ES_NUMBER = 0x00002000
ES_AUTOHSCROLL = 0x00000080

BG = 0x00F9F7F6          # COLORREF 00BBGGRR -> #f6f7f9
FG = 0x00211E1C          # #1c1e21
MUTED = 0x0080726B       # #6b7280
ACCENT = 0x00ED6F2F      # #2f6fed
GREEN = 0x0076660F       # #0f766e
AMBER = 0x000953B4       # #b45309

UI = {"hwnd": None, "state": None, "sub": None, "vid": None,
      "color": FG, "state_color": FG, "auto": None, "burst": None,
      "always": None, "always_state": None, "preview": None, "liblist": None,
      "hinst": None, "f_title": None, "f_big": None, "f_norm": None, "f_small": None,
      "bg_brush": None, "card_brush": None}

ID_PLAY, ID_SWITCH, ID_STOP, ID_START, ID_SAVE = 1001, 1002, 1003, 1004, 1005
ID_AUTO, ID_BURST, ID_ALWAYS, ID_INSTALL = 1006, 1007, 1008, 1009
ID_LIBLIST = 1010          # 视频库 ListBox（通知码走 WM_COMMAND）
ID_LIBUSE = 1011           # 「使用此视频」按钮

# 视频库列表：扫描视频目录得到的可选项
LIB = {"items": [],        # [(显示名, 视频绝对路径, 缩略图路径)]
       "preview_path": "", # 当前预览的缩略图 JPG 路径
       "preview_rect": None}  # 预览区在主窗口客户区的坐标 (x, y, w, h)


# ---- GDI+：把 JPG 缩略图转成 HBITMAP 给 Static 控件显示（系统自带，无第三方依赖）----
try:
    _gdip = ctypes.WinDLL("gdiplus")

    class _GdiPlusStartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                    ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", ctypes.c_int),
                    ("SuppressExternalCodecs", ctypes.c_int)]

    _gdip.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                                     ctypes.POINTER(_GdiPlusStartupInput), ctypes.c_void_p]
    _gdip.GdipLoadImageFromFile.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _gdip.GdipCreateHBITMAPFromBitmap.argtypes = [ctypes.c_void_p,
                                                  ctypes.POINTER(wintypes.HBITMAP),
                                                  wintypes.DWORD]   # ARGB 颜色
    _gdip.GdipDisposeImage.argtypes = [ctypes.c_void_p]
    _gdip.GdipCreateFromHDC.argtypes = [wintypes.HDC, ctypes.POINTER(ctypes.c_void_p)]
    _gdip.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _gdip.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
    _gdi_token = ctypes.c_size_t()
    _gdi_in = _GdiPlusStartupInput()
    _gdi_in.GdiplusVersion = 1
    _gdip_ok = (_gdip.GdiplusStartup(ctypes.byref(_gdi_token), ctypes.byref(_gdi_in), None) == 0)
except Exception:
    _gdip = None
    _gdip_ok = False


def jpg_to_hbitmap(path):
    """GDI+ 加载 JPG -> HBITMAP；失败返回 None"""
    if not (_gdip and _gdip_ok and path and os.path.exists(path)):
        return None
    img = ctypes.c_void_p()
    hbm = wintypes.HBITMAP()
    try:
        if _gdip.GdipLoadImageFromFile(path, ctypes.byref(img)) != 0 or not img.value:
            return None
        if _gdip.GdipCreateHBITMAPFromBitmap(img, ctypes.byref(hbm), 0xFFFFFFFF) != 0 or not hbm.value:
            return None
        return hbm.value
    except Exception:
        return None
    finally:
        if img.value:
            _gdip.GdipDisposeImage(img)


def _font(px, bold=False):
    return g32.CreateFontW(-int(px), 0, 0, 0, 700 if bold else 400, 0, 0, 0,
                           1, 0, 0, 5, 0, "Microsoft YaHei UI")


def _ctrl(parent, cls, text, style, x, y, w, h, cid=0):
    hwnd = u.CreateWindowExW(0, cls, text, WS_CHILD | WS_VISIBLE | style,
                             int(x), int(y), int(w), int(h), parent, cid,
                             UI["hinst"], None)
    return hwnd


def _setfont(h, f):
    u.SendMessageW(h, WM_SETFONT, f, 1)


def flash(text, secs=4.0):
    """在副标题行短暂显示一条操作反馈（refresh_ui 每秒刷新会覆盖它）"""
    UI["flash"] = (text, time.time() + secs)


# ---- 视频库：扫描 / 预览 / 应用 ----

def video_dir():
    """视频库目录：优先当前配置视频所在目录，其次 X:\动态壁纸视频"""
    try:
        d = os.path.dirname(os.path.abspath(load_cfg().get("video", "")))
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    lib = os.path.join("X:\\", "动态壁纸视频")
    return lib if os.path.isdir(lib) else APP_DIR


def friendly_name(path):
    """Africa_Night_1_4K.mov -> 非洲之夜？ 这里只做去后缀+下划线转空格"""
    s = os.path.splitext(os.path.basename(path))[0]
    return s.replace("_", " ")


def scan_library():
    """扫描视频目录，构建 LIB['items'] = [(显示名, 视频路径, 缩略图路径)]"""
    d = video_dir()
    thumb_dir = os.path.join(d, ".thumbs")
    items = []
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(VIDEO_EXT):
                continue
            p = os.path.join(d, fn)
            if not os.path.isfile(p):
                continue
            th = os.path.join(thumb_dir, os.path.splitext(fn)[0] + ".jpg")
            items.append((friendly_name(p), p, th if os.path.exists(th) else ""))
    except Exception:
        pass
    LIB["items"] = items
    return items


def show_preview(index):
    """记录第 index 个视频的缩略图路径，触发主窗口重绘"""
    items = LIB.get("items", [])
    LIB["preview_path"] = items[index][2] if (0 <= index < len(items)) else ""
    if UI.get("hwnd"):
        u.InvalidateRect(UI["hwnd"], None, True)


def draw_preview(hdc):
    """在预览区用 GDI+ 直接绘 JPG（绕开 Static 控件的 64 位位图句柄问题）"""
    rect = LIB.get("preview_rect")
    if not rect or not (_gdip and _gdip_ok):
        return
    x, y, w, h = rect
    # 白色底 + 边框
    br = g32.CreateSolidBrush(0x00FFFFFF)
    rc = wintypes.RECT(x, y, x + w, y + h)
    u.FillRect(hdc, ctypes.byref(rc), br)
    g32.DeleteObject(br)
    path = LIB.get("preview_path")
    if not path or not os.path.exists(path):
        return
    img = ctypes.c_void_p()
    if _gdip.GdipLoadImageFromFile(path, ctypes.byref(img)) != 0 or not img.value:
        return
    gfx = ctypes.c_void_p()
    try:
        if _gdip.GdipCreateFromHDC(hdc, ctypes.byref(gfx)) == 0 and gfx.value:
            # 等比缩放居中
            _gdip.GdipDrawImageRectI(gfx, img, x + 4, y + 4, w - 8, h - 8)
            _gdip.GdipDeleteGraphics(gfx)
    except Exception:
        pass
    finally:
        _gdip.GdipDisposeImage(img)


def apply_library_video(index):
    """把选中的视频设为壁纸（写配置 + nudge 让播放器热切换）"""
    items = LIB.get("items", [])
    if not (0 <= index < len(items)):
        return
    path = items[index][1]
    cfg = load_cfg()
    cfg["video"] = os.path.abspath(path)
    cfg["nudge"] = time.time()
    save_cfg(cfg)
    if not is_running():
        start_player()
    u.SetWindowTextW(UI["vid"], "当前视频：" + os.path.basename(path))
    flash(f"已设为壁纸：{friendly_name(path)}")


def refresh_ui():
    """WM_TIMER：刷新状态显示"""
    running = is_running()
    st = read_status()
    cfg = load_cfg()
    if running and st:
        if st.get("paused"):
            UI["color"] = GREEN
            txt = "状态：已冻结（定格最后一帧）"
            age = int(time.time() - float(st.get("ts", time.time())))
            sub = (f"静止 {age} 秒 · 开销≈0 · 回到桌面（亮屏/解锁/屏保退出）自动播放 "
                   f"{int(float(cfg.get('burst_seconds', 8)))} 秒")
        else:
            UI["color"] = ACCENT
            txt = "状态：正在播放"
            if cfg.get("always_play"):
                sub = "持续播放已开启：一直动，只有熄屏时才暂停"
            else:
                sub = "播放中（解码约 40% GPU），结束后自动冻结"
    elif running:
        UI["color"] = MUTED
        txt, sub = "状态：启动中…", ""
    else:
        UI["color"] = AMBER
        txt, sub = "状态：未运行", "点「启动壁纸」开始"
    u.SetWindowTextW(UI["state"], txt)
    fl = UI.get("flash")
    if fl and time.time() < fl[1]:
        sub = fl[0]
    u.SetWindowTextW(UI["sub"], sub)
    v = current_video()
    u.SetWindowTextW(UI["vid"], ("当前视频：" + os.path.basename(v)) if v
                     else "当前视频：未设置（把 mp4 放进本目录，或点「换视频…」）")
    # 复选框跟随配置（面板改完立刻写盘，所以这里不会抖动）
    if UI.get("always"):
        on = bool(cfg.get("always_play"))
        u.SendMessageW(UI["always"], BM_SETCHECK, 1 if on else 0, 0)
        u.SetWindowTextW(UI["always_state"], "持续播放：已开启" if on else "持续播放：未开启")


def auto_toggle_async(enable):
    """注册/移除登录自启（PowerShell 较慢，放后台线程，界面不卡）"""
    UI["auto_busy"] = True
    u.SetWindowTextW(UI["auto_state"], "自启：正在开启…" if enable else "自启：正在关闭…")

    def work():
        try:
            if enable:
                install_task()
            else:
                uninstall_task()
        except Exception:
            pass
        on = task_installed()
        u.SendMessageW(UI["auto"], BM_SETCHECK, 1 if on else 0, 0)
        u.SetWindowTextW(UI["auto_state"], "自启：" + ("已开启" if on else "未开启"))
        flash("开机自动运行已" + ("开启" if on else "关闭"))

    threading.Thread(target=work, daemon=True).start()


def ui_wndproc(hwnd, msg, wp, lp):
    if msg == WM_COMMAND:
        cid = wp & 0xFFFF
        if cid == ID_PLAY:
            if is_running():
                signal_player(EVT_PLAY)
                try:
                    secs = int(float(load_cfg().get("burst_seconds", 8)))
                except Exception:
                    secs = 8
                flash(f"已触发：播放 {secs} 秒后自动定格")
            else:
                start_player()
                flash("播放器未运行，已启动")
        elif cid == ID_SWITCH and _SWITCH_FN:
            _SWITCH_FN()
        elif cid == ID_STOP:
            flash("正在停止…", 2.0)
            ok = stop_player()
            flash("已停止，桌面定格为当前视频画面" if ok else "停止未完成，请再点一次", 5.0)
        elif cid == ID_START:
            flash("正在启动…", 2.0)
            start_player()
            flash("已启动，画面就绪后接管桌面", 5.0)
        elif cid == ID_SAVE:
            try:
                v = max(3, min(30, int(_get_edit_text(UI["burst"]) or 8)))
            except Exception:
                v = 8
            cfg = load_cfg(); cfg["burst_seconds"] = v; save_cfg(cfg)
            u.SetWindowTextW(UI["burst"], str(v))
            flash(f"已保存：回到桌面后播放 {v} 秒（立即生效）", 5.0)
        elif cid == ID_AUTO:
            # 复选框点击后状态已切换，读当前状态即为用户意图
            want = bool(u.SendMessageW(UI["auto"], BM_GETCHECK, 0, 0))
            auto_toggle_async(want)
        elif cid == ID_ALWAYS:
            want = bool(u.SendMessageW(UI["always"], BM_GETCHECK, 0, 0))
            cfg = load_cfg()
            cfg["always_play"] = want
            cfg["nudge"] = time.time()      # 立刻生效，不用等下一轮
            save_cfg(cfg)
            if want and not is_running():
                start_player()
            flash("已开启：壁纸持续播放，仅锁屏/熄屏/屏保时暂停" if want
                  else "已关闭：恢复为回到桌面播几秒后定格", 6.0)
        elif cid == ID_INSTALL:
            try:
                import screensaver
                ok, info = screensaver.install_screensaver()
            except Exception as e:
                ok, info = False, str(e)
            flash("屏保已安装并启用（等待 10 分钟触发；在系统屏保设置里可调整）" if ok
                  else "屏保安装失败：" + info, 7.0)
        elif cid == ID_LIBLIST:
            # ListBox 通知：LBN_SELCHANGE=1（点选预览），LBN_DBLCLK=2（双击设为壁纸）
            code = (wp >> 16) & 0xFFFF
            idx = u.SendMessageW(UI["liblist"], 0x0188, 0, 0)  # LB_GETCURSEL
            if code in (1, 2) and idx >= 0:
                show_preview(idx)
                if code == 2:
                    apply_library_video(idx)
        elif cid == ID_LIBUSE:
            idx = u.SendMessageW(UI["liblist"], 0x0188, 0, 0)
            if idx >= 0:
                apply_library_video(idx)
        u.InvalidateRect(UI["state"], None, True)
        refresh_ui()
        return 0
    if msg == WM_TIMER:
        refresh_ui()
        return 0
    if msg == WM_CTLCOLORSTATIC:
        hdc = wintypes.HDC(lp)
        g32.SetBkMode(hdc, 2)  # TRANSPARENT
        g32.SetTextColor(hdc, UI["color"] if lp == UI["state"] else FG)
        g32.SetBkColor(hdc, BG)
        return UI["bg_brush"]
    if msg == WM_ERASEBKGND:
        rect = wintypes.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rect))
        hdc = wintypes.HDC(wp)
        u.FillRect(hdc, ctypes.byref(rect), UI["bg_brush"])
        draw_preview(hdc)      # 视频库缩略图直接画在预览区
        return 1
    if msg == WM_CLOSE:
        u.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


def _get_edit_text(h):
    n = u.GetWindowTextLengthW(h) + 1
    buf = ctypes.create_unicode_buffer(n)
    u.GetWindowTextW(h, buf, n)
    return buf.value


_ui_proc = WNDPROC(ui_wndproc)


def run_ui():
    # 已有控制面板在跑则直接唤醒，避免多开
    exist = u.FindWindowW("AerialWallpaperUI", None)
    if exist:
        u.ShowWindow(exist, 9)
        u.SetForegroundWindow(exist)
        return

    try:
        u.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    g32.GetDeviceCaps.restype = ctypes.c_int
    g32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = [H]
    dc = u.GetDC(0)
    dpi = g32.GetDeviceCaps(dc, 88) or 96    # LOGPIXELSX
    u.ReleaseDC(0, dc)
    S = dpi / 96.0

    hinst = k32.GetModuleHandleW(None)
    UI["hinst"] = hinst
    wc = WNDCLASS()
    wc.lpfnWndProc = _ui_proc
    wc.hInstance = hinst
    wc.hbrBackground = None
    wc.lpszClassName = "AerialWallpaperUI"
    u.RegisterClassW(ctypes.byref(wc))

    # 左侧控制区 470 + 右侧视频库 400
    W, Ht = int(886 * S), int(600 * S)
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    hwnd = u.CreateWindowExW(0, "AerialWallpaperUI", "动态壁纸 · 控制面板",
                             0x00CF0000 | 0x00020000,   # OVERLAPPEDWINDOW | WS_MINIMIZEBOX
                             (sw - W) // 2, (sh - Ht) // 2, W, Ht,
                             None, None, hinst, None)
    if not hwnd:
        sys.exit("创建窗口失败")
    UI["hwnd"] = hwnd
    u.ShowWindow(hwnd, 9)          # SW_RESTORE：显示并激活窗口
    u.SetForegroundWindow(hwnd)
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)   # TOPMOST|NOMOVE|NOSIZE 确保不被遮挡
    UI["bg_brush"] = g32.CreateSolidBrush(BG)

    f_title = _font(15 * S, True); f_big = _font(12 * S, True)
    f_norm = _font(11 * S); f_small = _font(10 * S)
    UI["f_title"], UI["f_big"], UI["f_norm"], UI["f_small"] = f_title, f_big, f_norm, f_small

    M = int(18 * S)
    y = M

    t1 = _ctrl(hwnd, "STATIC", "动态壁纸", WS_CHILD | WS_VISIBLE, M, y, 300 * S, 24 * S)
    _setfont(t1, f_title); y += 28 * S
    t2 = _ctrl(hwnd, "STATIC", "回到桌面（亮屏/解锁/屏保退出）播放几秒，其余时间定格——几乎零开销",
               WS_CHILD | WS_VISIBLE, M, y, 430 * S, 18 * S)
    _setfont(t2, f_small); y += 26 * S

    UI["state"] = _ctrl(hwnd, "STATIC", "状态：读取中…", WS_CHILD | WS_VISIBLE, M, y, 430 * S, 22 * S)
    _setfont(UI["state"], f_big); y += 26 * S
    UI["sub"] = _ctrl(hwnd, "STATIC", "", WS_CHILD | WS_VISIBLE, M, y, 430 * S, 17 * S)
    _setfont(UI["sub"], f_small); y += 20 * S
    UI["vid"] = _ctrl(hwnd, "STATIC", "", WS_CHILD | WS_VISIBLE, M, y, 430 * S, 30 * S)
    _setfont(UI["vid"], f_small); y += 34 * S

    bw = (430 * S - 12 * S) / 2
    defs = [("立即动一下", ID_PLAY), ("换视频…", ID_SWITCH),
            ("停止壁纸", ID_STOP), ("启动壁纸", ID_START),
            ("安装屏保", ID_INSTALL)]
    rows = (len(defs) + 1) // 2
    for i, (txt, cid) in enumerate(defs):
        b = _ctrl(hwnd, "BUTTON", txt, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                  M + (i % 2) * (bw + 12 * S), y + (i // 2) * (34 * S + 6 * S), bw, 32 * S, cid)
        _setfont(b, f_norm)
    y += rows * (34 * S + 6 * S) + 6 * S

    UI["auto"] = _ctrl(hwnd, "BUTTON", "开机自动运行",
                       WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_TABSTOP,
                       M, y, 200 * S, 22 * S, ID_AUTO)
    _setfont(UI["auto"], f_norm)
    # 自启状态要问计划任务（PowerShell 约 1s），放到窗口显示后再查，
    # 否则界面会"半成品且不响应点击"约 1 秒
    UI["auto_state"] = _ctrl(hwnd, "STATIC", "自启：检查中…",
                             WS_CHILD | WS_VISIBLE, M + 210 * S, y + 3 * S, 200 * S, 18 * S)
    _setfont(UI["auto_state"], f_small)
    y += 32 * S

    UI["always"] = _ctrl(hwnd, "BUTTON", "让壁纸一直动",
                         WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_TABSTOP,
                         M, y, 200 * S, 22 * S, ID_ALWAYS)
    _setfont(UI["always"], f_norm)
    UI["always_state"] = _ctrl(hwnd, "STATIC", "持续播放：未开启",
                               WS_CHILD | WS_VISIBLE, M + 210 * S, y + 3 * S, 220 * S, 18 * S)
    _setfont(UI["always_state"], f_small)
    y += 32 * S

    lbl = _ctrl(hwnd, "STATIC", "回到桌面后播放（秒，3–30）", WS_CHILD | WS_VISIBLE, M, y + 3 * S,
                150 * S, 18 * S)
    _setfont(lbl, f_norm)
    UI["burst"] = _ctrl(hwnd, "EDIT", str(int(float(load_cfg().get("burst_seconds", 8)))),
                        WS_CHILD | WS_VISIBLE | WS_BORDER | ES_NUMBER | ES_AUTOHSCROLL | WS_TABSTOP,
                        M + 155 * S, y, 44 * S, 24 * S, ID_BURST)
    _setfont(UI["burst"], f_norm)
    bs = _ctrl(hwnd, "BUTTON", "保存", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
               M + 210 * S, y, 60 * S, 26 * S, ID_SAVE)
    _setfont(bs, f_norm)
    y += 38 * S

    foot = _ctrl(hwnd, "STATIC", f"目录：{APP_DIR}", WS_CHILD | WS_VISIBLE, M, y, 430 * S, 30 * S)
    _setfont(foot, f_small)

    # ---------------- 右侧：视频库列表 + 缩略预览 ----------------
    RX = int(482 * S)            # 右栏起点
    RW = int(386 * S)            # 右栏宽度
    sep = u.CreateWindowExW(0, "STATIC", None, WS_CHILD | WS_VISIBLE | 0x0001,  # SS_ETCHEDVERT
                            RX - int(8 * S), M, 2, Ht - 2 * M, None, None, hinst, None)
    lib_title = _ctrl(hwnd, "STATIC", "视频库（点击预览 · 双击设为壁纸）",
                      WS_CHILD | WS_VISIBLE, RX, M, RW, int(22 * S))
    _setfont(lib_title, f_big)
    ry = M + int(28 * S)

    # ListBox：LBS_NOTIFY(1) | LBS_HASSTRINGS(64) | WS_VSCROLL(0x200000) | WS_BORDER
    UI["liblist"] = u.CreateWindowExW(
        0, "ListBox", None,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | 0x0001 | 0x0040 | 0x00200000,
        RX, ry, RW, int(300 * S), hwnd, ID_LIBLIST, hinst, None)
    _setfont(UI["liblist"], f_norm)
    ry += int(306 * S)

    pv_label = _ctrl(hwnd, "STATIC", "预览", WS_CHILD | WS_VISIBLE, RX, ry, RW, int(18 * S))
    _setfont(pv_label, f_small)
    ry += int(20 * S)

    # 预览区：不再用 Static SS_BITMAP（跨进程/64 位句柄不可靠），
    # 直接在主窗口 WM_PAINT 中绘 JPG；这里只记坐标。
    LIB["preview_rect"] = (RX, ry, RW, int(180 * S))
    ry += int(188 * S)

    use = _ctrl(hwnd, "BUTTON", "使用此视频", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                RX, ry, int(RW / 2 - 4 * S), int(32 * S), ID_LIBUSE)
    _setfont(use, f_norm)

    # 填充列表：扫描视频目录，选中当前正在用的那条
    items = scan_library()
    LB_ADDSTRING, LB_SETCURSEL = 0x0180, 0x0186
    cur = os.path.basename(load_cfg().get("video", "")).lower()
    sel = 0
    for i, (disp, path, th) in enumerate(items):
        dbuf = ctypes.create_unicode_buffer(disp)
        u.SendMessageW(UI["liblist"], LB_ADDSTRING, 0,
                       wintypes.LPARAM(ctypes.addressof(dbuf)))
        if os.path.basename(path).lower() == cur:
            sel = i
    if items:
        u.SendMessageW(UI["liblist"], LB_SETCURSEL, sel, 0)
        show_preview(sel)

    # 换视频按钮的真实实现（Win32 打开文件对话框，无第三方依赖）
    def switch_video_dialog():
        p = pick_video()
        if p:
            cfg = load_cfg(); cfg["video"] = os.path.abspath(p); save_cfg(cfg)
            if not is_running():
                start_player()
            refresh_ui()

    # 把 ID_SWITCH 接到对话框（覆盖 WM_COMMAND 分支里的占位）
    global _SWITCH_FN
    _SWITCH_FN = switch_video_dialog
    refresh_ui()
    u.SetTimer(hwnd, 1, 1000, None)

    def init_auto_state():
        on = task_installed()
        if UI.get("auto_busy"):      # 用户已经手动改过了，别覆盖
            return
        u.SendMessageW(UI["auto"], BM_SETCHECK, 1 if on else 0, 0)
        u.SetWindowTextW(UI["auto_state"], "自启：" + ("已开启" if on else "未开启"))

    threading.Thread(target=init_auto_state, daemon=True).start()

    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


def _pick_initial_dir():
    """换视频对话框的初始目录：当前配置视频所在目录 → 视频库目录 → 程序目录"""
    try:
        cur = os.path.abspath(load_cfg().get("video", ""))
        d = os.path.dirname(cur)
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    lib = os.path.join("X:\\", "动态壁纸视频")
    if os.path.isdir(lib):
        return lib
    return APP_DIR


def pick_video():
    """comdlg32 打开文件对话框，返回所选路径或 None"""
    try:
        comdlg = ctypes.WinDLL("comdlg32", use_last_error=True)

        class OPENFILENAME(ctypes.Structure):
            _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", ctypes.c_void_p),
                        ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR)]

        buf = ctypes.create_unicode_buffer(1024)
        ofn = OPENFILENAME()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
        ofn.hwndOwner = UI["hwnd"]
        ofn.lpstrFilter = "视频 (*.mp4;*.mov;*.webm;*.mkv;*.m4v)\0*.mp4;*.mov;*.webm;*.mkv;*.m4v\0所有文件\0*.*\0"
        ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
        ofn.nMaxFile = 1024
        ofn.lpstrTitle = "选择壁纸视频"
        ofn.lpstrInitialDir = _pick_initial_dir()
        ofn.Flags = 0x00001000 | 0x00000800 | 0x00000004   # FILEMUSTEXIST|PATHMUSTEXIST|HIDEREADONLY
        if comdlg.GetOpenFileNameW(ctypes.byref(ofn)):
            return buf.value
    except Exception as e:
        print("pick_video:", e, file=sys.stderr)
    return None


_SWITCH_FN = None


def ensure_console():
    if not FROZEN:
        return
    try:
        if not k32.AttachConsole(-1):
            k32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    # 屏幕保护程序调用约定：/s 全屏 /c 配置 /p <hwnd> 预览；
    # 无参数且以 .scr 运行时按 /s 处理（复制出的 AerialScreensaver.scr 走这里）
    scr_flags = {"/s", "-s", "/c", "-c", "/p", "-p", "/l"}
    if any(a.lower() in scr_flags for a in args) or \
            (not args and sys.executable.lower().endswith(".scr")):
        import screensaver
        screensaver.main()
        return
    if "--run" in args:
        import wallpaper_player
        wallpaper_player.main()
        return
    cli = any(a in args for a in ("--install", "--uninstall", "--stop", "--status"))
    if cli:
        ensure_console()
    if "--install" in args:
        print("自启注册:", "成功" if install_task() else "失败")
        return
    if "--uninstall" in args:
        uninstall_task()
        print("自启已移除")
        return
    if "--stop" in args:
        stop_player()
        print("已停止")
        return
    if "--status" in args:
        print(json.dumps({"running": is_running(), "status": read_status(),
                          "config": load_cfg()}, ensure_ascii=False, indent=2))
        return
    run_ui()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        try:
            with open(os.path.join(APP_DIR, "app_error.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise
