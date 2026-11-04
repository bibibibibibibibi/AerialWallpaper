# -*- coding: utf-8 -*-
"""面板按钮端到端测试：向真实面板窗口的控件发送 BM_CLICK，验证实际效果"""
import ctypes, json, os, subprocess, sys, time
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
u = ctypes.WinDLL("user32")
k32 = ctypes.WinDLL("kernel32")
H = wintypes.HWND
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.FindWindowW.restype = H
u.EnumChildWindows.argtypes = [H, ctypes.c_void_p, wintypes.LPARAM]
u.GetDlgCtrlID.argtypes = [H]
u.SendMessageW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.SendMessageW.restype = ctypes.c_longlong
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010

IDS = {"play": 1001, "switch": 1002, "stop": 1003, "start": 1004,
       "save": 1005, "auto": 1006, "burst": 1007, "always": 1008, "install": 1009}
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
results = []

def set_text(h, s):
    """跨进程写控件文本：SetWindowTextW 只改系统缓存副本、不会真正写入
    控件（面板进程内读到的仍是旧值），必须用 WM_SETTEXT 消息"""
    buf = ctypes.create_unicode_buffer(s)
    return u.SendMessageW(h, WM_SETTEXT, 0, wintypes.LPARAM(ctypes.addressof(buf)))

def get_text(h):
    """跨进程读控件真实文本：WM_GETTEXT 消息（GetWindowTextW 跨进程读的是缓存）"""
    n = u.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0) + 1
    buf = ctypes.create_unicode_buffer(max(n, 64))
    u.SendMessageW(h, WM_GETTEXT, max(n, 64), wintypes.LPARAM(ctypes.addressof(buf)))
    return buf.value

def panel():
    return u.FindWindowW("AerialWallpaperUI", None)

def controls():
    hwnd = panel()
    out = {}
    if not hwnd:
        return out
    @ctypes.WINFUNCTYPE(wintypes.BOOL, H, wintypes.LPARAM)
    def cb(h, lp):
        cid = u.GetDlgCtrlID(h)
        if cid:
            out[cid] = h
        return True
    u.EnumChildWindows(hwnd, ctypes.cast(cb, ctypes.c_void_p), 0)
    return out

def click(name):
    c = controls()
    cid = IDS[name]
    if cid not in c:
        raise RuntimeError(f"控件 {name}({cid}) 未找到, 现有: {sorted(c)}")
    u.SendMessageW(c[cid], BM_CLICK, 0, 0)

def cfg():
    return json.load(open("config.json", encoding="utf-8"))

def status():
    try:
        return json.load(open("status.json", encoding="utf-8"))
    except Exception:
        return {}

def procs():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {"player": out.count("AerialWallpaper.exe"), "mpv": out.count("mpv.exe")}

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}", flush=True)

def wait(pred, timeout=8, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False

def report():
    n = sum(1 for _, ok, _ in results if ok)
    print(f"\n==== 结果: {n}/{len(results)} 通过 ====")
    for name, ok, detail in results:
        if not ok:
            print("  FAILED:", name, detail)

def step_a():
    print("A. 设置亮屏播放=5秒，测「立即动一下」", flush=True)
    c = controls()
    set_text(c[IDS["burst"]], "5")
    click("save")
    time.sleep(2)
    check("A1 秒数写入配置", cfg().get("burst_seconds") == 5, f"实际={cfg().get('burst_seconds')}")
    check("A1b 面板回显一致", get_text(c[IDS["burst"]]) == "5",
          f"实际={get_text(c[IDS['burst']])!r}")
    click("play")
    time.sleep(2)
    check("A2 动一下后正在播放", status().get("paused") is False, str(status())[:90])
    import wallpaper_player as wp
    p1 = wp.ipc_get("time-pos", 1.5)
    time.sleep(3)
    p2 = wp.ipc_get("time-pos", 1.5)
    check("A3 画面确实推进", isinstance(p1, float) and isinstance(p2, float) and p2 > p1,
          f"{p1} -> {p2}")
    time.sleep(5)
    check("A4 5秒后自动定格", status().get("paused") is True, str(status())[:90])

def step_b():
    print("B. 「让壁纸一直动」开关", flush=True)
    if cfg().get("always_play", False):
        click("always"); time.sleep(2)
    click("always")
    time.sleep(2)
    check("B1 开启写入配置", cfg().get("always_play") is True)
    time.sleep(7)
    check("B2 超过burst仍播放", status().get("paused") is False, str(status())[:90])
    click("always")
    time.sleep(2)
    check("B3 关闭写入配置", cfg().get("always_play") is False)
    time.sleep(7)
    check("B4 关闭后按时定格", status().get("paused") is True, str(status())[:90])

def step_c():
    print("C. 停止/启动/动一下组合", flush=True)
    click("stop")
    ok = wait(lambda: procs()["mpv"] == 0, 10)
    check("C1 停止后 mpv 退出", ok, str(procs()))
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
        wall = winreg.QueryValueEx(k, "WallPaper")[0]
    check("C2 桌面定格为当前视频画面", wall.endswith(".stopframe.jpg"), wall)
    click("play")
    ok = wait(lambda: procs()["mpv"] >= 1, 15)
    check("C3 停止后点动一下能启动", ok, str(procs()))
    click("stop")
    wait(lambda: procs()["mpv"] == 0, 10)
    click("start")
    ok = wait(lambda: procs()["mpv"] >= 1, 15)
    check("C4 启动壁纸按钮", ok, str(procs()))

def step_d():
    print("D. 换视频对话框 + 安装屏保", flush=True)
    import winreg
    v0 = cfg().get("video")
    click("switch")
    time.sleep(2)
    dlg = u.FindWindowW("#32770", None)
    if dlg:
        u.SendMessageW(dlg, WM_CLOSE, 0, 0)
    time.sleep(1)
    check("D1 换视频弹出文件对话框", bool(dlg))
    check("D2 取消后配置不变", cfg().get("video") == v0)
    check("D3 面板仍存活", bool(panel()))
    scr = os.path.join(BASE, "AerialScreensaver.scr")
    old = os.path.getmtime(scr) if os.path.exists(scr) else 0
    click("install")
    time.sleep(3)
    check("D4 屏保文件已生成/更新", os.path.exists(scr) and os.path.getmtime(scr) >= old)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
        reg = winreg.QueryValueEx(k, "SCRNSAVE.EXE")[0]
        act = winreg.QueryValueEx(k, "ScreenSaveActive")[0]
    check("D5 注册表屏保已启用", reg.endswith("AerialScreensaver.scr") and act == "1", reg)

def step_e():
    print("E. 组合: 一直动→停止→启动", flush=True)
    if not cfg().get("always_play"):
        click("always"); time.sleep(2)
    click("stop")
    wait(lambda: procs()["mpv"] == 0, 10)
    click("start")
    wait(lambda: procs()["mpv"] >= 1, 15)
    time.sleep(8)
    check("E1 重启后仍一直动", status().get("paused") is False and cfg().get("always_play") is True,
          str(status())[:90])
    click("always")
    time.sleep(2)

def restore():
    c = cfg()
    c["burst_seconds"] = 30
    c["always_play"] = False
    c["nudge"] = time.time()
    json.dump(c, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已恢复: burst=30, always_play=False")

if __name__ == "__main__":
    if not panel():
        print("面板未运行!")
        sys.exit(1)
    for s in sys.argv[1:]:
        if s == "restore":
            continue
        globals()["step_" + s]()
    if "restore" in sys.argv:
        restore()
    report()
