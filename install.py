# -*- coding: utf-8 -*-
"""
一键安装 / 移植：把整个 aerial 文件夹拷到新机器后跑这一个脚本即可

  python install.py            # 安装并启动
  python install.py --uninstall
  python install.py --package  # 打包成可拷贝的 zip（含 mpv，目标机免装 Lively）

做的事：
  1. 找 mpv：优先 本目录/mpv/mpv.exe，其次 Lively 安装目录，找到就复制进本目录（自包含）
  2. 写 config.json（视频 + mpv 路径）
  3. 注册计划任务「视频动态壁纸」= 登录时自启
  4. 立即启动
"""
import os
import sys
import json
import shutil
import subprocess
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
MUTEX_TASK = "视频动态壁纸"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")

LIVELY_MPV_DIRS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Lively Wallpaper\Plugins\Mpv"),
    os.path.expandvars(r"%PROGRAMFILES%\Lively Wallpaper\Plugins\Mpv"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Lively Wallpaper\Plugins\Mpv"),
]


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def pythonw():
    p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return p if os.path.exists(p) else sys.executable


def ensure_mpv():
    local = os.path.join(HERE, "mpv", "mpv.exe")
    if os.path.exists(local):
        print("[1/4] mpv: 使用本目录 mpv/（自包含）")
        return local
    for d in LIVELY_MPV_DIRS:
        src = os.path.join(d, "mpv.exe")
        if os.path.exists(src):
            dst = os.path.join(HERE, "mpv")
            os.makedirs(dst, exist_ok=True)
            for f in os.listdir(d):                      # 连依赖 dll 一起搬，保证可移植
                if os.path.isfile(os.path.join(d, f)):
                    shutil.copy2(os.path.join(d, f), os.path.join(dst, f))
            print(f"[1/4] mpv: 已从 Lively 复制到 {dst}")
            return os.path.join(dst, "mpv.exe")
    w = shutil.which("mpv")
    if w:
        print("[1/4] mpv: 使用 PATH 中的 mpv（建议复制到本目录 mpv/ 以自包含）")
        return w
    sys.exit("[1/4] 失败：找不到 mpv.exe。请安装 Lively Wallpaper，或把 mpv.exe 及其 dll "
             "放到本目录 mpv/ 下后重试。")


def ensure_video():
    cfg = {}
    if os.path.exists(CONFIG):
        try:
            cfg = json.load(open(CONFIG, "r", encoding="utf-8"))
        except Exception:
            cfg = {}
    v = cfg.get("video")
    if v and os.path.exists(v):
        print(f"[2/4] 视频: {v}")
        return v, cfg
    vids = [os.path.join(HERE, n) for n in sorted(os.listdir(HERE)) if n.lower().endswith(VIDEO_EXT)]
    if not vids:
        sys.exit("[2/4] 失败：本目录没有视频文件（mp4/mov/webm/mkv/m4v）")
    print(f"[2/4] 视频: {vids[0]}（目录内第一个）")
    return vids[0], cfg


def write_config(video, mpv, cfg):
    cfg["video"] = video
    cfg["mpv"] = mpv
    # 低功耗默认值：亮屏后播 8s 再冻结；空闲 20s 回来再播一次；不周期播放
    cfg.setdefault("burst_seconds", 8)
    cfg.setdefault("idle_trigger_seconds", 20)
    cfg.setdefault("refresh_minutes", 0)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("[3/4] 已写 config.json")


def register_task():
    exe = pythonw()
    script = os.path.join(HERE, "wallpaper_player.py")
    user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    # 先结束旧实例，否则新实例会被单实例锁挡掉
    ps("Get-Process mpv -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() }; "
       "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' Or Name='python.exe'\" | "
       "Where-Object { $_.CommandLine -like '*wallpaper_player.py*' } | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    cmd = f"""
$ErrorActionPreference='Stop'
Unregister-ScheduledTask -TaskName '{MUTEX_TASK}' -Confirm:$false -ErrorAction SilentlyContinue
$act = New-ScheduledTaskAction -Execute '{exe}' -Argument '"{script}"' -WorkingDirectory '{HERE}'
$trg = New-ScheduledTaskTrigger -AtLogOn -User '{user}'
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$prn = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{MUTEX_TASK}' -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
Start-ScheduledTask -TaskName '{MUTEX_TASK}'
'registered'
"""
    r = ps(cmd)
    if "registered" in (r.stdout or ""):
        print("[4/4] 计划任务已注册并启动（登录时自动运行）")
    else:
        print("[4/4] 注册失败：\n" + (r.stdout or "") + (r.stderr or ""))


def uninstall():
    ps("Get-Process mpv -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() }; "
       "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' Or Name='python.exe'\" | "
       "Where-Object { $_.CommandLine -like '*wallpaper_player.py*' } | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    ps(f"Unregister-ScheduledTask -TaskName '{MUTEX_TASK}' -Confirm:$false -ErrorAction SilentlyContinue")
    print("已卸载：任务已删除，壁纸进程已结束。")


def package():
    mpv = ensure_mpv()
    out = os.path.join(os.path.dirname(HERE), "aerial_wallpaper_portable.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(HERE):
            for f in files:
                if f.lower().endswith((".png", ".json.old")) or f.startswith("desktop_"):
                    continue
                p = os.path.join(root, f)
                z.write(p, os.path.join("aerial", os.path.relpath(p, HERE)))
                n += 1
    print(f"已打包 {n} 个文件 -> {out}  ({os.path.getsize(out)/1e6:.0f} MB)")
    print("目标机解压后：python install.py")


def main():
    if "--uninstall" in sys.argv:
        return uninstall()
    if "--package" in sys.argv:
        return package()
    print(f"安装目录: {HERE}\nPython: {sys.executable}\n")
    mpv = ensure_mpv()
    video, cfg = ensure_video()
    write_config(video, mpv, cfg)
    register_task()


if __name__ == "__main__":
    main()
