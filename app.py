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
import subprocess
from ctypes import wintypes

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG = os.path.join(APP_DIR, "config.json")
STATUS = os.path.join(APP_DIR, "status.json")
TASK = "视频动态壁纸"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
MUTEX = "Global\\AerialWallpaperPlayer"

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


def start_player():
    if is_running():
        return
    if FROZEN:
        cmd = [sys.executable, "--run"]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        cmd = [exe, os.path.join(APP_DIR, "wallpaper_player.py")]
    subprocess.Popen(cmd, cwd=APP_DIR, close_fds=True,
                     creationflags=0x00000008 | 0x08000000)


def stop_player():
    pattern = "*--run*" if FROZEN else "*wallpaper_player.py*"
    filt = "Name='AerialWallpaper.exe'" if FROZEN else "Name='python.exe' Or Name='pythonw.exe'"
    ps("Get-Process mpv -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() }; "
       f"Get-CimInstance Win32_Process -Filter \"{filt}\" | "
       f"Where-Object {{ $_.CommandLine -like '{pattern}' }} | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    # 恢复用户原来的静态壁纸（首次启动播放器时记录的）
    orig = load_cfg().get("original_wallpaper")
    if orig and os.path.exists(orig):
        ctypes.windll.user32.SystemParametersInfoW(20, 0, orig, 0x01 | 0x02)


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
g32.CreateFontW.restype = wintypes.HFONT
g32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wintypes.LPCWSTR]
u.FillRect.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.HBRUSH]
u.SendMessageW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
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
BM_GETCHECK = 0x00F2
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
      "hinst": None, "f_title": None, "f_big": None, "f_norm": None, "f_small": None,
      "bg_brush": None, "card_brush": None}

ID_PLAY, ID_SWITCH, ID_STOP, ID_START, ID_SAVE = 1001, 1002, 1003, 1004, 1005
ID_AUTO, ID_BURST = 1006, 1007


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
            sub = (f"静止 {age} 秒 · 开销≈0 · 亮屏/解锁/回来时会自动播放 "
                   f"{int(float(cfg.get('burst_seconds', 8)))} 秒")
        else:
            UI["color"] = ACCENT
            txt = "状态：正在播放"
            sub = "播放中（解码约 40% GPU），结束后自动冻结"
    elif running:
        UI["color"] = MUTED
        txt, sub = "状态：启动中…", ""
    else:
        UI["color"] = AMBER
        txt, sub = "状态：未运行", "点「启动壁纸」开始"
    u.SetWindowTextW(UI["state"], txt)
    u.SetWindowTextW(UI["sub"], sub)
    v = current_video()
    u.SetWindowTextW(UI["vid"], ("当前视频：" + os.path.basename(v)) if v
                     else "当前视频：未设置（把 mp4 放进本目录，或点「换视频…」）")


def ui_wndproc(hwnd, msg, wp, lp):
    if msg == WM_COMMAND:
        cid = wp & 0xFFFF
        if cid == ID_PLAY:
            cfg = load_cfg(); cfg["nudge"] = time.time(); save_cfg(cfg)
        elif cid == ID_SWITCH and _SWITCH_FN:
            _SWITCH_FN()
        elif cid == ID_STOP:
            stop_player()
        elif cid == ID_START:
            start_player()
        elif cid == ID_SAVE:
            try:
                v = max(3, min(30, int(_get_edit_text(UI["burst"]) or 8)))
            except Exception:
                v = 8
            cfg = load_cfg(); cfg["burst_seconds"] = v; save_cfg(cfg)
        elif cid == ID_AUTO:
            if u.SendMessageW(UI["auto"], BM_GETCHECK, 0, 0):
                ok = install_task()
                if not ok:
                    u.SendMessageW(UI["auto"], BM_SETCHECK, 0, 0)
                u.SetWindowTextW(UI["auto_state"], "自启：" + ("已开启" if ok else "注册失败"))
            else:
                uninstall_task()
                u.SetWindowTextW(UI["auto_state"], "自启：未开启")
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
        u.FillRect(wintypes.HDC(wp), ctypes.byref(rect), UI["bg_brush"])
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

    W, Ht = int(470 * S), int(430 * S)
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
    t2 = _ctrl(hwnd, "STATIC", "亮屏 / 解锁后播放几秒，其余时间定格——几乎零开销",
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
            ("停止壁纸", ID_STOP), ("启动壁纸", ID_START)]
    for i, (txt, cid) in enumerate(defs):
        b = _ctrl(hwnd, "BUTTON", txt, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                  M + (i % 2) * (bw + 12 * S), y + (i // 2) * (34 * S + 6 * S), bw, 32 * S, cid)
        _setfont(b, f_norm)
    y += 2 * (34 * S + 6 * S) + 6 * S

    UI["auto"] = _ctrl(hwnd, "BUTTON", "开机自动运行",
                       WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_TABSTOP,
                       M, y, 200 * S, 22 * S, ID_AUTO)
    _setfont(UI["auto"], f_norm)
    _auto_init = task_installed()
    u.SendMessageW(UI["auto"], BM_SETCHECK, 1 if _auto_init else 0, 0)
    UI["auto_state"] = _ctrl(hwnd, "STATIC", "自启：" + ("已开启" if _auto_init else "未开启"),
                             WS_CHILD | WS_VISIBLE, M + 210 * S, y + 3 * S, 200 * S, 18 * S)
    _setfont(UI["auto_state"], f_small)
    y += 32 * S

    lbl = _ctrl(hwnd, "STATIC", "亮屏后播放（秒，3–30）", WS_CHILD | WS_VISIBLE, M, y + 3 * S,
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

    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


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
