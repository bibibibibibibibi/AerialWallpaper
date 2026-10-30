# -*- coding: utf-8 -*-
"""
换片工具：改写 config.json，播放器 3 秒内自动切到新视频（无需重启、不闪屏）

用法:
  python switch_video.py                      # 显示当前配置 + 可选视频列表
  python switch_video.py D:/x/beijing.mp4     # 换片
  python switch_video.py --stop               # 停止视频壁纸（回到系统静态壁纸）
  python switch_video.py --start              # 启动视频壁纸
"""
import os
import sys
import json
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
TASK = "视频动态壁纸"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def load():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_videos():
    found = []
    for d in (HERE, os.path.join(os.path.expanduser("~"), "Videos")):
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.lower().endswith(VIDEO_EXT):
                    p = os.path.join(d, n)
                    found.append((p, os.path.getsize(p) / 1e6))
    return found


def ps(cmd):
    """跑一条 PowerShell（换片/启停只需这几个 cmdlet）"""
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def is_running():
    r = ps("(Get-Process mpv -ErrorAction SilentlyContinue | Measure-Object).Count")
    try:
        return int((r.stdout or "0").strip() or 0) > 0
    except ValueError:
        return False


def stop():
    ps("Get-Process mpv -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() }; "
       "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' Or Name='python.exe'\" | "
       "Where-Object { $_.CommandLine -like '*wallpaper_player.py*' } | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    ps(f"Stop-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue")
    print("已停止：桌面回到系统静态壁纸。")


def start():
    r = ps(f"Start-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue; "
           f"if ($?) {{ 'ok' }}")
    if "ok" in (r.stdout or ""):
        print("已启动。")
    else:
        print("计划任务未找到，直接前台启动…")
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable,
                          os.path.join(HERE, "wallpaper_player.py")], cwd=HERE)


def main():
    args = [a for a in sys.argv[1:]]

    if "--stop" in args:
        return stop()
    if "--start" in args:
        return start()
    if "--nudge" in args:
        cfg = load()
        cfg["nudge"] = time.time()
        save(cfg)
        print("已触发：壁纸播一遍后自动冻结。")
        return
    if "--status" in args:
        st = os.path.join(HERE, "status.json")
        if os.path.exists(st):
            print(json.dumps(json.load(open(st, encoding="utf-8")), ensure_ascii=False, indent=2))
        else:
            print("没有状态文件（播放器未运行？）")
        return

    cfg = load()
    if not args:
        cur = cfg.get("video") or "（未设置，播放目录内第一个视频）"
        print("当前: " + cur)
        print("运行中: " + ("是" if is_running() else "否"))
        print("\n可用视频:")
        for p, mb in list_videos():
            mark = " *" if os.path.normcase(p) == os.path.normcase(cur) else "  "
            print(f" {mark} {p}  ({mb:.0f} MB)")
        print("\n换片: python switch_video.py <视频路径>")
        return

    path = args[0]
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.exists(path):
        sys.exit("文件不存在: " + path)
    if not path.lower().endswith(VIDEO_EXT):
        sys.exit("不支持的扩展名（支持 " + " ".join(VIDEO_EXT) + "）")

    cfg["video"] = path
    save(cfg)
    print("已写入配置: " + path)

    if not is_running():
        print("播放器未运行，正在启动…")
        start()
        return

    print("播放器运行中，等待热切换（最多 8 秒）…")
    for _ in range(16):
        time.sleep(0.5)
        r = ps("Get-CimInstance Win32_Process -Filter \"Name='mpv.exe'\" | "
               "Select-Object -ExpandProperty CommandLine")
        if path.lower() in (r.stdout or "").lower():
            print("切换完成。")
            return
    print("已下发，但未在预期时间内确认。若没变化，执行: python switch_video.py --start")


if __name__ == "__main__":
    main()
