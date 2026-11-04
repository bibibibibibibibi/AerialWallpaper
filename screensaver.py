# -*- coding: utf-8 -*-
"""
动态壁纸 · 屏幕保护程序

同一个 exe 复制为 AerialScreensaver.scr 后即可被 Windows 当作屏保调用：
  /s          全屏运行（系统空闲触发、双击测试）
  /c          配置对话框（屏保设置里的"设置"按钮）
  /p <hwnd>   在设置页面的小窗里实时预览
无参数且文件名为 .scr 时按 /s 处理（部分启动路径不带参数）。

行为：全屏置顶黑底窗口 + mpv 嵌入播放（与壁纸共用视频库/配置）；
启动时让壁纸进程暂停（省一路 4K 解码），退出时唤醒恢复。
鼠标移动（超过阈值）/ 任意按键 / 点击 -> 退出屏保。
"""
import os
import sys
import time
import json
import ctypes
import subprocess
from ctypes import wintypes

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from wallpaper_player import EVT_PAUSE, EVT_PLAY  # noqa: E402

SAVER_NAME = "AerialWallpaperSaver"
SAVER_TITLE = "AerialVideoSaver"
MOVE_THRESHOLD = 4          # 鼠标移动超过这个像素才算"动"，忽略抖动
SCR_SAVE_AS = "AerialScreensaver.scr"


def load_cfg():
    try:
        with open(os.path.join(APP_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def find_mpv(cfg):
    mpv = cfg.get("mpv") or os.path.join(APP_DIR, "mpv", "mpv.exe")
    return mpv if os.path.exists(mpv) else None


def find_video(cfg):
    v = cfg.get("video")
    if v and os.path.exists(v):
        return v
    # 没配置就取目录里第一条视频
    for n in sorted(os.listdir(APP_DIR)):
        if n.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v")):
            return os.path.join(APP_DIR, n)
    return None


def signal_event(name):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    h = k32.OpenEventW(0x0002, False, name)      # EVENT_MODIFY_STATE
    if not h:
        return False
    ok = bool(k32.SetEvent(ctypes.c_void_p(h)))
    k32.CloseHandle(ctypes.c_void_p(h))
    return ok


def launch_mpv(hwnd, video, cfg):
    mpv = find_mpv(cfg)
    if not mpv:
        raise SystemExit("找不到 mpv.exe")
    return subprocess.Popen(
        [mpv, f"--wid={hwnd}", "--loop=inf", "--no-audio", "--really-quiet",
         "--no-osd-bar", "--osd-level=0", "--hwdec=auto-safe",
         "--keepaspect=yes", "--panscan=0.0",
         "--no-input-default-bindings", "--no-osc", "--border=no",
         video],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ---------------------------------------------------------------- 全屏屏保

u = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

H = wintypes.HWND
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u.DefWindowProcW.restype = ctypes.c_long
u.DefWindowProcW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.CreateWindowExW.restype = H
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              H, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.LPVOID]
u.RegisterClassW.argtypes = [ctypes.c_void_p]
u.ShowWindow.restype = wintypes.BOOL
u.ShowWindow.argtypes = [H, ctypes.c_int]
u.SetWindowPos.restype = wintypes.BOOL
u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.GetSystemMetrics.restype = ctypes.c_int
u.GetSystemMetrics.argtypes = [ctypes.c_int]
u.GetMessageW.restype = wintypes.BOOL
u.GetMessageW.argtypes = [ctypes.c_void_p, H, wintypes.UINT, wintypes.UINT]
u.PostQuitMessage.argtypes = [ctypes.c_int]
u.DestroyWindow.argtypes = [H]
u.SetCursor.restype = ctypes.c_void_p
u.SetCursor.argtypes = [ctypes.c_void_p]
u.LoadCursorW.restype = ctypes.c_void_p
u.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
u.GetMessagePos.restype = ctypes.c_long
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_SETCURSOR = 0x0020

EXIT = {"done": False}


def _wndproc_saver(hwnd, msg, wp, lp):
    if msg == WM_MOUSEMOVE:
        x = ctypes.c_short(lp & 0xFFFF).value       # 屏幕坐标（低位 x 高位 y）
        y = ctypes.c_short((lp >> 16) & 0xFFFF).value
        base = EXIT.get("mouse0")
        if base is None:
            EXIT["mouse0"] = (x, y)                 # 第一条 move 只记基准
        else:
            if abs(x - base[0]) > MOVE_THRESHOLD or abs(y - base[1]) > MOVE_THRESHOLD:
                u.PostQuitMessage(0)
        return 0
    if msg in (WM_LBUTTONDOWN, WM_KEYDOWN, WM_SYSKEYDOWN):
        if wp == 0x1B or msg == WM_LBUTTONDOWN or msg == WM_KEYDOWN:   # Esc / 任意键 / 点击
            u.PostQuitMessage(0)
            return 0
    if msg == WM_CLOSE:
        u.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


_proc_ref = WNDPROC(_wndproc_saver)   # 防 GC


def run_fullscreen():
    try:
        u.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    cfg = load_cfg()
    video = find_video(cfg)
    if not video:
        ctypes.windll.user32.MessageBoxW(0, "没有可播放的视频（config.json 的 video 不存在，目录里也没有视频文件）。",
                                         SAVER_TITLE, 0x30)
        return
    # 壁纸进程正在播的话先让它静下来，省一路 4K 解码
    signal_event(EVT_PAUSE)

    hinst = k32.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.lpfnWndProc = _proc_ref
    wc.hInstance = hinst
    wc.hCursor = u.LoadCursorW(None, ctypes.c_wchar_p(32512))   # IDC_ARROW
    wc.hbrBackground = 1                                        # BLACK_BRUSH
    wc.lpszClassName = SAVER_NAME
    u.RegisterClassW(ctypes.byref(wc))

    # 覆盖整个虚拟桌面（多显示器全包）
    vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)

    hwnd = u.CreateWindowExW(
        0x00000008 | 0x00000080,      # WS_EX_TOPMOST | WS_EX_TOOLWINDOW（不进 Alt-Tab）
        SAVER_NAME, SAVER_TITLE,
        0x80000000,                   # WS_POPUP（无边框全屏）
        vx, vy, vw, vh,
        None, None, hinst, None)
    if not hwnd:
        return
    u.ShowWindow(hwnd, 5)             # SW_SHOW

    mpv_proc = None
    try:
        mpv_proc = launch_mpv(hwnd, video, cfg)
        u.SetCursor(None)             # 隐藏光标（一动就退出，无需恢复路径）
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
    finally:
        if mpv_proc:
            try:
                mpv_proc.terminate()
                mpv_proc.wait(timeout=3)
            except Exception:
                pass
        try:
            u.DestroyWindow(hwnd)
        except Exception:
            pass
        # 屏保退出 -> 唤醒壁纸继续播
        signal_event(EVT_PLAY)


# ---------------------------------------------------------------- 预览 / 配置

def run_preview(parent_hwnd):
    """设置页面的小窗实时预览：mpv 直接嵌进父窗口"""
    cfg = load_cfg()
    video = find_video(cfg)
    if not video:
        return
    try:
        mpv_proc = launch_mpv(parent_hwnd, video, cfg)
    except SystemExit:
        return
    try:
        while True:
            time.sleep(0.2)
            if not u.IsWindow(parent_hwnd) or mpv_proc.poll() is not None:
                break
    finally:
        try:
            mpv_proc.terminate()
            mpv_proc.wait(timeout=3)
        except Exception:
            pass


def show_config():
    ctypes.windll.user32.MessageBoxW(
        0,
        "本屏幕保护与「动态壁纸」共用同一视频库和设置：\n\n"
        "  · 换视频 / 加视频：双击桌面的「动态壁纸控制面板」\n"
        "  · 建议等待时间 5–15 分钟，视频全程无声\n"
        "  · 屏保运行期间动态壁纸会自动暂停，退出后自动恢复\n\n"
        "动一动鼠标或按任意键即可退出屏保。",
        SAVER_TITLE, 0x40)


# ---------------------------------------------------------------- 安装 / 卸载

def install_screensaver():
    """把当前 exe 复制为 .scr 并设为当前屏保（HKCU，不写 System32 以免权限问题）"""
    import shutil
    import winreg
    src = sys.executable if FROZEN else os.path.join(APP_DIR, "AerialWallpaper.exe")
    if not os.path.exists(src):
        return False, "找不到 exe"
    dst = os.path.join(APP_DIR, SCR_SAVE_AS)
    shutil.copyfile(src, dst)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "SCRNSAVE.EXE", 0, winreg.REG_SZ, dst)
        winreg.SetValueEx(k, "ScreenSaveActive", 0, winreg.REG_SZ, "1")
        winreg.SetValueEx(k, "ScreenSaveTimeOut", 0, winreg.REG_SZ, "600")
    return True, dst


def uninstall_screensaver():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, "SCRNSAVE.EXE")
            except FileNotFoundError:
                pass
            winreg.SetValueEx(k, "ScreenSaveActive", 0, winreg.REG_SZ, "0")
    except Exception:
        return False
    return True


def screensaver_installed():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            v, _ = winreg.QueryValueEx(k, "SCRNSAVE.EXE")
            return bool(v)
    except Exception:
        return False


def main():
    args = sys.argv[1:]
    low = [a.lower() for a in args]

    if any(a in low for a in ("/p", "-p")):
        i = low.index("/p") if "/p" in low else low.index("-p")
        try:
            hwnd = int(args[i + 1])
        except (IndexError, ValueError):
            return
        run_preview(hwnd)
        return
    if any(a in low for a in ("/c", "-c")):
        show_config()
        return
    if any(a in low for a in ("/s", "-s", "/l")) or \
            (not args and sys.executable.lower().endswith(".scr")):
        run_fullscreen()
        return


if __name__ == "__main__":
    main()
