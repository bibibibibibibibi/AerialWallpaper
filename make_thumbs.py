# -*- coding: utf-8 -*-
"""为视频库批量生成缩略图（面板视频库预览用）

对每个 .mov 抽取第 2 秒的一帧，缩放 320x180，JPEG 存到 .thumbs/ 子目录。
缩略图文件名与视频同名（.jpg）。已存在且尺寸正常则跳过。
"""
import os
import subprocess
import sys
import time

FFMPEG = (r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12"
          r"\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe")
VIDEO_DIR = r"X:\动态壁纸视频"
THUMB_DIR = os.path.join(VIDEO_DIR, ".thumbs")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thumbs.log")


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    os.makedirs(THUMB_DIR, exist_ok=True)
    videos = sorted(f for f in os.listdir(VIDEO_DIR)
                    if f.lower().endswith((".mov", ".mp4", ".mkv", ".webm")))
    log(f"=== 缩略图生成开始：{len(videos)} 条 ===")
    ok = fail = skip = 0
    for i, v in enumerate(videos, 1):
        src = os.path.join(VIDEO_DIR, v)
        dst = os.path.join(THUMB_DIR, os.path.splitext(v)[0] + ".jpg")
        if os.path.exists(dst) and os.path.getsize(dst) > 3000:
            skip += 1
            continue
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", "2", "-i", src, "-frames:v", "1",
               "-vf", "scale=320:180:force_original_aspect_ratio=decrease,"
                      "pad=320:180:(ow-iw)/2:(oh-ih)/2",
               "-q:v", "4", dst]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=90)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 3000:
                ok += 1
                log(f"[{i}/{len(videos)}] OK {v}")
            else:
                fail += 1
                log(f"[{i}/{len(videos)}] FAIL {v}: {r.stderr.decode('utf-8', 'ignore')[:120]}")
        except Exception as e:
            fail += 1
            log(f"[{i}/{len(videos)}] ERR {v}: {e}")
    log(f"=== 完成：新建 {ok}，跳过 {skip}，失败 {fail} ===")


if __name__ == "__main__":
    main()
