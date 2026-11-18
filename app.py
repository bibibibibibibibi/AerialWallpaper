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
g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
g32.SelectObject.restype = wintypes.HGDIOBJ
g32.GetStockObject.argtypes = [ctypes.c_int]
g32.GetStockObject.restype = wintypes.HGDIOBJ
g32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                      ctypes.POINTER(wintypes.SIZE)]
u.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                        ctypes.POINTER(wintypes.RECT), wintypes.UINT]
u.SetWindowLongPtrW.argtypes = [H, ctypes.c_int, ctypes.c_ssize_t]
u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
u.CallWindowProcW.argtypes = [ctypes.c_ssize_t, H, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM]
u.CallWindowProcW.restype = ctypes.c_ssize_t


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", H), ("dwHoverTime", wintypes.DWORD)]


class DRAWITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
                ("itemID", wintypes.UINT), ("itemAction", wintypes.UINT),
                ("itemState", wintypes.UINT), ("hwndItem", H),
                ("hDC", wintypes.HDC), ("rcItem", wintypes.RECT),
                ("itemData", ctypes.c_size_t)]


class MEASUREITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
                ("itemID", wintypes.UINT), ("itemWidth", wintypes.UINT),
                ("itemHeight", wintypes.UINT), ("itemData", ctypes.c_size_t)]
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
WM_CTLCOLOREDIT = 0x0133
WM_DRAWITEM = 0x002B
WM_MEASUREITEM = 0x002C
WM_MOUSEMOVE = 0x0200
WM_MOUSELEAVE = 0x02A3
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_BORDER = 0x00800000
WS_CLIPCHILDREN = 0x02000000
BS_AUTOCHECKBOX = 0x00000003
BS_OWNERDRAW = 0x0000000B
ES_NUMBER = 0x00002000
ES_AUTOHSCROLL = 0x00000080

# ---- 浅色现代卡片风配色（COLORREF 00BBGGRR）----
BG = 0x00F6F1EE          # 窗口背景 #EEF1F6
CARD = 0x00FFFFFF        # 卡片白
FG = 0x00241D1A          # 主文字 #1A1D24
MUTED = 0x0094847A       # 次要文字 #7A8494
ACCENT = 0x00ED6F2F      # 主蓝 #2F6FED
ACCENT_HOVER = 0x00F0804A  # 悬停亮蓝 #4A80F0
ACCENT_DOWN = 0x00D05F25   # 按下深蓝 #255FD0
BORDER = 0x00EEE7E3      # 卡片描边 #E3E7EE
GREEN = 0x006C9D0F       # 播放绿 #0F9D6C
GREEN_BG = 0x00EEF6E5    # 胶囊浅绿底 #E5F6EE
GRAY = 0x00A3938A        # 定格灰 #8A93A3
GRAY_BG = 0x00F5F0ED     # 胶囊浅灰底 #EDF0F5
AMBER = 0x000677D9       # 未运行琥珀 #D97706
AMBER_BG = 0x00E3F1FD    # 胶囊浅琥珀底 #FDF1E3
SEL_BG = 0x00FFEDE3      # 列表选中浅蓝 #E3EDFF

UI = {"hwnd": None, "state": None, "sub": None, "vid": None,
      "color": FG, "state_color": FG, "auto": None, "burst": None,
      "always": None, "always_state": None, "preview": None, "liblist": None,
      "hinst": None, "f_title": None, "f_big": None, "f_norm": None, "f_small": None,
      "bg_brush": None, "card_brush": None, "null_brush": None,
      "scale": 1.0, "badge": ("未运行", "off"), "badge_sig": None,
      "hover_btn": None, "btn_oldproc": {}, "muted": set(), "cards": [],
      "ctrl_bg": {}}

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
    # 圆角卡片 / 按钮 / 胶囊所需的 GDI+ 路径 API
    _gdip.GdipCreateSolidFill.argtypes = [ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
    _gdip.GdipDeleteBrush.argtypes = [ctypes.c_void_p]
    _gdip.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    _gdip.GdipDeletePath.argtypes = [ctypes.c_void_p]
    _gdip.GdipAddPathArc.argtypes = [ctypes.c_void_p] + [ctypes.c_float] * 6
    _gdip.GdipClosePathFigure.argtypes = [ctypes.c_void_p]
    _gdip.GdipFillPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _gdip.GdipDrawPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _gdip.GdipCreatePen1.argtypes = [ctypes.c_int32, ctypes.c_float, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_void_p)]
    _gdip.GdipDeletePen.argtypes = [ctypes.c_void_p]
    _gdip.GdipSetSmoothingMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
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


def _argb(colorref):
    """COLORREF(00BBGGRR) -> GDI+ ARGB 有符号 int32（不透明）"""
    r = colorref & 0xFF
    g = (colorref >> 8) & 0xFF
    b = (colorref >> 16) & 0xFF
    v = 0xFF000000 | (r << 16) | (g << 8) | b
    return v - 0x100000000 if v >= 0x80000000 else v


def round_rect(hdc, x, y, w, h, r, fill=None, line=None, line_w=1.0):
    """GDI+ 抗锯齿圆角矩形。fill/line 为 COLORREF，None 表示不画"""
    if not (_gdip and _gdip_ok):
        if fill is not None:
            br = g32.CreateSolidBrush(fill)
            rc = wintypes.RECT(int(x), int(y), int(x + w), int(y + h))
            u.FillRect(hdc, ctypes.byref(rc), br)
            g32.DeleteObject(br)
        return
    gfx = ctypes.c_void_p()
    if _gdip.GdipCreateFromHDC(hdc, ctypes.byref(gfx)) != 0 or not gfx.value:
        return
    path = ctypes.c_void_p()
    try:
        _gdip.GdipSetSmoothingMode(gfx, 4)          # SmoothingModeAntiAlias
        r = max(1.0, min(float(r), w / 2.0, h / 2.0))
        d = 2.0 * r
        if _gdip.GdipCreatePath(0, ctypes.byref(path)) != 0 or not path.value:
            return
        _gdip.GdipAddPathArc(path, x, y, d, d, 180, 90)
        _gdip.GdipAddPathArc(path, x + w - d, y, d, d, 270, 90)
        _gdip.GdipAddPathArc(path, x + w - d, y + h - d, d, d, 0, 90)
        _gdip.GdipAddPathArc(path, x, y + h - d, d, d, 90, 90)
        _gdip.GdipClosePathFigure(path)
        if fill is not None:
            br = ctypes.c_void_p()
            if _gdip.GdipCreateSolidFill(_argb(fill), ctypes.byref(br)) == 0:
                _gdip.GdipFillPath(gfx, br, path)
                _gdip.GdipDeleteBrush(br)
        if line is not None:
            pen = ctypes.c_void_p()
            if _gdip.GdipCreatePen1(_argb(line), float(line_w), 2, ctypes.byref(pen)) == 0:
                _gdip.GdipDrawPath(gfx, pen, path)
                _gdip.GdipDeletePen(pen)
    except Exception:
        pass
    finally:
        if path.value:
            _gdip.GdipDeletePath(path)
        _gdip.GdipDeleteGraphics(gfx)


def draw_text(hdc, text, x, y, w, h, color, font, center=False):
    """单行文字，垂直居中；center=True 时水平居中"""
    g32.SetBkMode(hdc, 1)                            # TRANSPARENT(1)，2=OPAQUE 会产生白字白底块
    g32.SetTextColor(hdc, color)
    old = g32.SelectObject(hdc, font)
    rc = wintypes.RECT(int(x), int(y), int(x + w), int(y + h))
    u.DrawTextW(hdc, text, -1, ctypes.byref(rc),
                0x0020 | 0x0004 | (0x0001 if center else 0))   # SINGLELINE|VCENTER
    g32.SelectObject(hdc, old)


def text_width(hdc, text, font):
    old = g32.SelectObject(hdc, font)
    sz = wintypes.SIZE()
    g32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
    g32.SelectObject(hdc, old)
    return sz.cx


BADGE_STYLE = {           # kind -> (点/文字颜色, 底色)
    "play": (GREEN, GREEN_BG),
    "pause": (GRAY, GRAY_BG),
    "off": (AMBER, AMBER_BG),
    "starting": (GRAY, GRAY_BG),
}


def draw_badge(hdc):
    """右上角状态胶囊：圆点 + 文字，浅底色药丸"""
    if not UI.get("badge_x") or not UI.get("f_norm"):
        return                      # 窗口创建早期、布局未完成时不画
    text, kind = UI.get("badge", ("未运行", "off"))
    dot, bgc = BADGE_STYLE.get(kind, BADGE_STYLE["off"])
    S = UI["scale"]
    f = UI["f_norm"]
    tw = text_width(hdc, text, f)
    w = int(tw + 46 * S)
    h = int(30 * S)
    x = UI["badge_x"] - w
    y = int(26 * S)
    UI["badge_rect"] = (x - 4, y - 4, w + 8, h + 8)
    round_rect(hdc, x, y, w, h, h / 2.0, fill=bgc)
    # 圆点
    dr = int(4 * S)
    dx = x + int(14 * S)
    dy = y + (h - 2 * dr) // 2
    round_rect(hdc, dx, dy, 2 * dr, 2 * dr, dr, fill=dot)
    draw_text(hdc, text, dx + 2 * dr + int(7 * S), y, tw + 8, h, dot, f)


def draw_button(dis):
    """owner-draw 按钮：主按钮实心蓝、次按钮白底描边，含悬停/按下态"""
    cid = dis.CtlID
    S = UI["scale"]
    rc = dis.rcItem
    x, y, w, h = rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top
    pressed = bool(dis.itemState & 0x0008)           # ODS_SELECTED
    hovered = UI.get("hover_btn") == dis.hwndItem
    primary = cid in (1001, 1011)                    # 立即动一下 / 使用此视频
    n = u.GetWindowTextLengthW(dis.hwndItem) + 1
    buf = ctypes.create_unicode_buffer(n)
    u.GetWindowTextW(dis.hwndItem, buf, n)
    if primary:
        fill = ACCENT_DOWN if pressed else (ACCENT_HOVER if hovered else ACCENT)
        fg = 0x00FFFFFF
        round_rect(dis.hDC, x, y, w, h, 8 * S, fill=fill)
    else:
        fill = 0x00FBF6F3 if hovered else CARD       # hover #F3F6FB
        round_rect(dis.hDC, x, y, w, h, 8 * S, fill=fill, line=BORDER)
        fg = FG
    draw_text(dis.hDC, buf.value, x, y - 1, w, h, fg, UI["f_norm"], center=True)


def draw_list_item(dis):
    """视频库行：白底，选中行浅蓝圆角，文字垂直居中"""
    if dis.itemID == 0xFFFFFFFF:
        return
    S = UI["scale"]
    rc = dis.rcItem
    x, y, w, h = rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top
    br = g32.CreateSolidBrush(CARD)
    u.FillRect(dis.hDC, ctypes.byref(rc), br)
    g32.DeleteObject(br)
    selected = bool(dis.itemState & 0x0001)          # ODS_SELECTED
    if selected:
        round_rect(dis.hDC, x + 4, y + 2, w - 8, h - 4, 6 * S, fill=SEL_BG)
    # LBS_HASSTRINGS 下列表文字由控件持有，itemData 恒为 0；必须用 LB_GETTEXT 取回
    txt = ""
    try:
        n = u.SendMessageW(dis.hwndItem, 0x018A, dis.itemID, 0)     # LB_GETTEXTLEN
        if 0 < n < 4096:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.SendMessageW(dis.hwndItem, 0x0189, dis.itemID,        # LB_GETTEXT
                           wintypes.LPARAM(ctypes.addressof(buf)))
            txt = buf.value
    except Exception:
        txt = ""
    color = ACCENT if selected else FG
    draw_text(dis.hDC, txt, x + int(16 * S), y, w - int(28 * S), h, color, UI["f_norm"])


def _btn_wndproc(hwnd, msg, wp, lp):
    """按钮子类化：追踪悬停（owner-draw 按钮的 hover 效果）"""
    if msg == WM_MOUSEMOVE:
        if UI.get("hover_btn") != hwnd:
            UI["hover_btn"] = hwnd
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x0002, hwnd, 0)  # TME_LEAVE
            u.TrackMouseEvent(ctypes.byref(tme))
        u.InvalidateRect(hwnd, None, False)
        return 0
    if msg == WM_MOUSELEAVE:
        if UI.get("hover_btn") == hwnd:
            UI["hover_btn"] = None
        u.InvalidateRect(hwnd, None, False)
        return 0
    old = UI["btn_oldproc"].get(hwnd)
    if old:
        return u.CallWindowProcW(old, hwnd, msg, wp, lp)
    return u.DefWindowProcW(hwnd, msg, wp, lp)


_BTN_PROC = WNDPROC(_btn_wndproc)     # 保持回调引用，防 GC


def make_ownerdraw_button(b):
    """把按钮切到自绘 + 子类化以支持悬停"""
    old = u.SetWindowLongPtrW(b, -4, ctypes.cast(_BTN_PROC, ctypes.c_void_p).value)  # GWLP_WNDPROC
    UI["btn_oldproc"][b] = old


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
    # 圆角白底卡片 + 描边
    round_rect(hdc, x, y, w, h, 12 * UI["scale"], fill=CARD, line=BORDER)
    path = LIB.get("preview_path")
    if not path or not os.path.exists(path):
        draw_text(hdc, "无缩略图", x, y, w, h, MUTED, UI["f_small"], center=True)
        return
    img = ctypes.c_void_p()
    if _gdip.GdipLoadImageFromFile(path, ctypes.byref(img)) != 0 or not img.value:
        return
    gfx = ctypes.c_void_p()
    try:
        if _gdip.GdipCreateFromHDC(hdc, ctypes.byref(gfx)) == 0 and gfx.value:
            # 等比缩放居中（缩略图固定 320x180）
            sc = min((w - 12) / 320.0, (h - 12) / 180.0)
            dw, dh = int(320 * sc), int(180 * sc)
            _gdip.GdipDrawImageRectI(gfx, img, x + (w - dw) // 2, y + (h - dh) // 2, dw, dh)
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
    """WM_TIMER：刷新状态胶囊、当前视频、按钮文字"""
    running = is_running()
    st = read_status()
    cfg = load_cfg()
    try:
        burst = int(float(cfg.get("burst_seconds", 8)))
    except Exception:
        burst = 8
    if running and st:
        if st.get("paused"):
            badge = ("已定格", "pause")
            age = int(time.time() - float(st.get("ts", time.time())))
            sub = f"静止 {age} 秒 · 开销≈0 · 回到桌面自动播放 {burst} 秒"
        else:
            badge = ("正在播放", "play")
            if cfg.get("always_play"):
                sub = "持续播放已开启 · 锁屏/熄屏/屏保时暂停，回到桌面自动恢复"
            else:
                sub = f"播放中 · {burst} 秒后自动定格"
    elif running:
        badge, sub = ("启动中", "starting"), "正在接管桌面…"
    else:
        badge, sub = ("未运行", "off"), "点「启动壁纸」开始，或双击右侧视频库"
    if UI.get("badge") != badge:
        UI["badge"] = badge
        if UI.get("hwnd") and UI.get("badge_x"):
            S = UI["scale"]
            rc = wintypes.RECT(UI["badge_x"] - int(250 * S), int(16 * S),
                               UI["badge_x"] + 8, int(68 * S))
            u.InvalidateRect(UI["hwnd"], ctypes.byref(rc), True)
    # 智能切换按钮：文字跟随运行状态（每秒刷新，无抖动）
    if UI.get("toggle"):
        u.SetWindowTextW(UI["toggle"], "停止壁纸" if running else "启动壁纸")
    fl = UI.get("flash")
    if fl and time.time() < fl[1]:
        sub = fl[0]
    u.SetWindowTextW(UI["sub"], sub)
    v = current_video()
    u.SetWindowTextW(UI["vid"], os.path.basename(v) if v
                     else "未设置（双击右侧视频库选择）")
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
        elif cid == ID_STOP:
            # 智能切换：运行中 -> 停止（桌面定格当前帧）；已停止 -> 启动
            if is_running():
                flash("正在停止…", 2.0)
                ok = stop_player()
                flash("已停止，桌面定格为当前视频画面" if ok else "停止未完成，请再点一次", 5.0)
            else:
                flash("正在启动…", 2.0)
                start_player()
                flash("已启动，画面就绪后接管桌面", 5.0)
                if UI.get("toggle"):
                    u.SetWindowTextW(UI["toggle"], "停止壁纸")
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
        refresh_ui()
        return 0
    if msg == WM_TIMER:
        refresh_ui()
        return 0
    if msg == WM_MEASUREITEM:
        mis = ctypes.cast(lp, ctypes.POINTER(MEASUREITEMSTRUCT)).contents
        if mis.CtlType == 2:                       # ODT_LISTBOX
            mis.itemHeight = int(30 * UI["scale"])
            return 1
        return 0
    if msg == WM_DRAWITEM:
        dis = ctypes.cast(lp, ctypes.POINTER(DRAWITEMSTRUCT)).contents
        if dis.CtlType == 4:                       # ODT_BUTTON
            draw_button(dis)
            return 1
        if dis.CtlType == 2:                       # ODT_LISTBOX
            draw_list_item(dis)
            return 1
        return 0
    if msg == WM_CTLCOLORSTATIC:
        # 不透明方案：文字背景 = 所在卡片颜色，避免透明文字的残影问题
        hdc = wintypes.HDC(wp)
        bgc = UI["ctrl_bg"].get(lp, BG)
        g32.SetBkMode(hdc, 2)                      # OPAQUE
        g32.SetBkColor(hdc, bgc)
        g32.SetTextColor(hdc, MUTED if lp in UI["muted"] else FG)
        return UI["card_brush"] if bgc == CARD else UI["bg_brush"]
    if msg == WM_CTLCOLORBTN:
        # 复选框：文字背景用卡片白
        hdc = wintypes.HDC(wp)
        g32.SetBkMode(hdc, 2)
        g32.SetBkColor(hdc, CARD)
        g32.SetTextColor(hdc, FG)
        return UI["card_brush"]
    if msg == WM_CTLCOLOREDIT:
        hdc = wintypes.HDC(wp)
        g32.SetBkColor(hdc, CARD)
        g32.SetTextColor(hdc, FG)
        return UI["card_brush"]
    if msg == WM_ERASEBKGND:
        rect = wintypes.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rect))
        hdc = wintypes.HDC(wp)
        u.FillRect(hdc, ctypes.byref(rect), UI["bg_brush"])
        for (cx, cy, cw, ch) in UI["cards"]:
            round_rect(hdc, cx, cy, cw, ch, 12 * UI["scale"], fill=CARD, line=BORDER)
        draw_badge(hdc)
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

    # 左侧控制区 452 + 右侧视频库 436
    W, Ht = int(960 * S), int(644 * S)
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    hwnd = u.CreateWindowExW(0, "AerialWallpaperUI", "动态壁纸 · 控制面板",
                             0x00CF0000 | 0x00020000 | WS_CLIPCHILDREN,
                             (sw - W) // 2, (sh - Ht) // 2, W, Ht,
                             None, None, hinst, None)
    if not hwnd:
        sys.exit("创建窗口失败")
    UI["hwnd"] = hwnd
    u.ShowWindow(hwnd, 9)          # SW_RESTORE：显示并激活窗口
    u.SetForegroundWindow(hwnd)
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)   # TOPMOST|NOMOVE|NOSIZE 确保不被遮挡
    UI["bg_brush"] = g32.CreateSolidBrush(BG)
    UI["card_brush"] = g32.CreateSolidBrush(CARD)
    UI["scale"] = S

    f_title = _font(22 * S, True); f_big = _font(13 * S, True)
    f_norm = _font(11 * S); f_small = _font(10 * S)
    UI["f_title"], UI["f_big"], UI["f_norm"], UI["f_small"] = f_title, f_big, f_norm, f_small

    def static(text, x, y, w, h, font=None, muted=False, card=False):
        c = _ctrl(hwnd, "STATIC", text, 0, x, y, w, h)
        _setfont(c, font or f_norm)
        if muted:
            UI["muted"].add(c)
        if card:
            UI["ctrl_bg"][c] = CARD
        return c

    def button(text, cid, x, y, w, h):
        b = _ctrl(hwnd, "BUTTON", text, WS_TABSTOP | BS_OWNERDRAW, x, y, w, h, cid)
        _setfont(b, f_norm)
        make_ownerdraw_button(b)
        return b

    def card(x, y, w, h):
        UI["cards"].append((int(x), int(y), int(w), int(h)))

    M = 24 * S
    LW = 452 * S

    # ---------------- 顶部：标题 + 状态胶囊（胶囊自绘于右上） ----------------
    static("动态壁纸", M, 20 * S, 400 * S, 34 * S, f_title)
    static("回到桌面自动播放几秒，其余时间定格 · 几乎零开销",
           M, 58 * S, 440 * S, 18 * S, f_small, muted=True)
    UI["badge_x"] = int(960 * S - M)

    # ---------------- 卡片 A：当前壁纸 ----------------
    card(M, 92 * S, LW, 96 * S)
    static("当前壁纸", M + 18 * S, 105 * S, LW - 36 * S, 16 * S, f_small, muted=True, card=True)
    UI["vid"] = static("", M + 18 * S, 124 * S, LW - 36 * S, 24 * S, f_big, card=True)
    UI["sub"] = static("", M + 18 * S, 154 * S, LW - 36 * S, 18 * S, f_small, muted=True, card=True)

    # ---------------- 卡片 B：快捷操作 ----------------
    card(M, 200 * S, LW, 118 * S)
    button("立即动一下", ID_PLAY, M + 16 * S, 216 * S, LW - 32 * S, 36 * S)
    bw2 = (LW - 42 * S) / 2
    UI["toggle"] = button("停止壁纸", ID_STOP, M + 16 * S, 262 * S, bw2, 32 * S)
    button("安装屏保", ID_INSTALL, M + 26 * S + bw2, 262 * S, bw2, 32 * S)

    # ---------------- 卡片 C：播放设置 ----------------
    card(M, 330 * S, LW, 130 * S)
    UI["auto"] = _ctrl(hwnd, "BUTTON", "开机自动运行", BS_AUTOCHECKBOX | WS_TABSTOP,
                       M + 16 * S, 344 * S, 180 * S, 24 * S, ID_AUTO)
    _setfont(UI["auto"], f_norm)
    UI["auto_state"] = static("自启：检查中…", M + 210 * S, 348 * S, 210 * S, 18 * S,
                              f_small, muted=True, card=True)
    UI["always"] = _ctrl(hwnd, "BUTTON", "让壁纸一直动", BS_AUTOCHECKBOX | WS_TABSTOP,
                         M + 16 * S, 376 * S, 180 * S, 24 * S, ID_ALWAYS)
    _setfont(UI["always"], f_norm)
    UI["always_state"] = static("持续播放：未开启", M + 210 * S, 380 * S, 220 * S, 18 * S,
                                f_small, muted=True, card=True)
    static("回到桌面后播放（秒，3–30）", M + 18 * S, 416 * S, 205 * S, 20 * S, f_norm, card=True)
    UI["burst"] = _ctrl(hwnd, "EDIT", str(int(float(load_cfg().get("burst_seconds", 8)))),
                        WS_BORDER | ES_NUMBER | ES_AUTOHSCROLL | WS_TABSTOP,
                        M + 232 * S, 412 * S, 48 * S, 26 * S, ID_BURST)
    _setfont(UI["burst"], f_norm)
    button("保存", ID_SAVE, M + 292 * S, 412 * S, 64 * S, 28 * S)

    static(f"目录：{APP_DIR}", M, 474 * S, LW, 30 * S, f_small, muted=True)

    # ---------------- 右侧：视频库列表 + 缩略预览 ----------------
    RX = 500 * S
    RW = 436 * S
    # 标题宽度收窄，给右上角状态胶囊让位（STATIC 不透明背景会盖住父窗口自绘的徽章）
    head_w = RW - 150 * S
    static("视频库", RX, 24 * S, head_w, 24 * S, f_big)
    static("点击预览 · 双击设为壁纸", RX, 52 * S, head_w, 18 * S, f_small, muted=True)

    # ListBox：LBS_NOTIFY(1)|LBS_HASSTRINGS(0x40)|LBS_OWNERDRAWFIXED(0x10)
    #          |LBS_NOINTEGRALHEIGHT(0x100)|WS_VSCROLL(0x200000)
    UI["liblist"] = u.CreateWindowExW(
        0, "ListBox", None,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | 0x00200000 | 0x0001 | 0x0040 | 0x0010 | 0x0100,
        int(RX), int(92 * S), int(RW), int(280 * S), hwnd, ID_LIBLIST, hinst, None)
    _setfont(UI["liblist"], f_norm)

    # 预览区：圆角白底卡片，GDI+ 直接绘 JPG（见 draw_preview）
    LIB["preview_rect"] = (int(RX), int(384 * S), int(RW), int(160 * S))
    button("使用此视频", ID_LIBUSE, RX, 556 * S, RW, 34 * S)

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
