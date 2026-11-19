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
from preferences import ConfigError, read_config, update_config, write_config

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG = os.path.join(APP_DIR, "config.json")
STATUS = os.path.join(APP_DIR, "status.json")
TASK = "视频动态壁纸"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
MUTEX = "Global\\AerialWallpaperPlayer"
EVT_STOP = "Global\\AerialWallpaper_ctl_stop"
EVT_PLAY = "Global\\AerialWallpaper_ctl_play"
_CONFIG_LOCK = threading.RLock()

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


# ---------------------------------------------------------------- 基础工具

def ps(cmd, timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (r.stdout or "") + (r.stderr or "")
        return output if r.returncode == 0 else output + f"\nERR PowerShell exited {r.returncode}"
    except Exception as e:
        return f"ERR {e}"


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def load_cfg():
    # Corrupt existing data must not silently turn into an empty configuration.
    return read_config(CONFIG)


def save_cfg(cfg):
    with _CONFIG_LOCK:
        write_config(CONFIG, cfg)


def update_cfg(**changes):
    """Merge fresh keys atomically, coordinated with the player's startup save."""
    with _CONFIG_LOCK:
        return update_config(CONFIG, changes)


def read_status():
    try:
        with open(STATUS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
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
    # Keep startup failures visible when launched by the pythonw shortcut.
    with open(os.path.join(APP_DIR, "player-startup.log"), "ab") as errors:
        subprocess.Popen(cmd, cwd=APP_DIR, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=errors, stderr=subprocess.STDOUT,
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
        ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ("
           "($_.Name -eq 'mpv.exe' -and $_.CommandLine -like '*aerial_wallpaper_mpv*') -or "
           "($_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -like '*wallpaper_player.py*') -or "
           "($_.Name -eq 'AerialWallpaper.exe' -and $_.CommandLine -match '(^|\\s)--run(\\s|$)')) } | "
           "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        wait_stopped(2.0)
        # Freezing is best-effort: corrupt preferences must never prevent stop.
        try:
            if not freeze_to_video():
                restore_original()
        except (OSError, ValueError, ConfigError):
            pass
    return not is_running()


def task_installed():
    result = ps("$ErrorActionPreference='Stop'; "
                f"if (Get-ScheduledTask -ErrorAction Stop | Where-Object {{ $_.TaskName -eq {_ps_quote(TASK)} }}) "
                "{ 'YES' } else { 'NO' }")
    lines = result.strip().splitlines()
    if lines and lines[-1].strip() in ("YES", "NO"):
        return lines[-1].strip() == "YES"
    raise RuntimeError("无法检查登录自启状态，请稍后重试")


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
$act = New-ScheduledTaskAction -Execute {_ps_quote(exe)} -Argument {_ps_quote(args)} -WorkingDirectory {_ps_quote(APP_DIR)}
$trg = New-ScheduledTaskTrigger -AtLogOn -User {_ps_quote(user)}
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$prn = New-ScheduledTaskPrincipal -UserId {_ps_quote(user)} -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName {_ps_quote(TASK)} -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
'ok'
"""
    lines = ps(cmd).strip().splitlines()
    return bool(lines and lines[-1].strip() == "ok")


def uninstall_task():
    result = ps(f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK)} -Confirm:$false -ErrorAction Stop; 'ok'")
    lines = result.strip().splitlines()
    return bool(lines and lines[-1].strip() == "ok")


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


# ------------------------------------------------------------ Preferences UI

def run_ui():
    # Keep Win32 drawing separate from player/CLI services. The panel accepts
    # this module as its service boundary, so tests need no live system changes.
    import preferences_panel
    preferences_panel.run_ui(sys.modules[__name__])


def install_screensaver():
    import screensaver
    return screensaver.install_screensaver()


def ensure_console():
    if not FROZEN:
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    if any(a.lower().split(":", 1)[0] in scr_flags for a in args) or \
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
        print("自启移除:", "成功" if uninstall_task() else "失败")
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
