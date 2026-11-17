# -*- coding: utf-8 -*-
"""视频库扩充（二）：纯自然风光（2026-11-17）

应「多点自然风光」追加 16 条：中国山水 6 条、格陵兰 2 条、夏威夷 4 条、
帕劳珊瑚、红海珊瑚、阿拉斯加水母、加州海藻林。全部 4K。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import fetch_aerial as fa

DEST_DIR = r"X:\动态壁纸视频"

PICKS = [
    # ---- 中国山水 / 长城 / 乡村（现有 China_20 之外的不同机位）----
    (16, "China_16_4K.mov"),
    (17, "China_17_4K.mov"),
    (18, "China_18_4K.mov"),
    (19, "China_19_4K.mov"),
    (21, "China_21_4K.mov"),
    (22, "China_22_4K.mov"),
    # ---- 格陵兰冰川（现有 Greenland_32 之外）----
    (31, "Greenland_31_4K.mov"),
    (33, "Greenland_33_4K.mov"),
    # ---- 夏威夷火山/海岸（现有 Hawaii_37 之外）----
    (34, "Hawaii_34_4K.mov"),
    (35, "Hawaii_35_4K.mov"),
    (36, "Hawaii_36_4K.mov"),
    (38, "Hawaii_38_4K.mov"),
    # ---- 海底自然 ----
    (3,  "Alaskan_Jellies_3_4K.mov"),   # 阿拉斯加水母
    (70, "Palau_Coral_70_4K.mov"),      # 帕劳珊瑚礁
    (74, "Red_Sea_Coral_74_4K.mov"),    # 红海珊瑚
    (11, "CA_Kelp_Forest_11_4K.mov"),   # 加州海藻林
]

LOG = os.path.join(HERE, "expand_nature.log")


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(url, name):
    dest = os.path.join(DEST_DIR, name)
    size = fa.head_size(url)
    if size <= 0:
        log(f"SKIP {name}: 拿不到大小（URL 失效？）")
        return False
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        log(f"已存在 {name}（{size/1048576:.0f}MB），跳过")
        return True
    log(f"下载 {name}（{size/1048576:.0f}MB）...")
    try:
        fa.download(url, dest, size)
        log(f"完成 {name}")
        return True
    except SystemExit as e:
        log(f"失败 {name}: {e}")
        return False


def main():
    log("=== 自然风光扩充开始（16 条）===")
    items = {x["idx"]: x for x in fa.load_catalog()}
    ok = []
    for idx, name in PICKS:
        it = items.get(idx)
        if it:
            if fetch(it["u4k"], name):
                ok.append(name)
        else:
            log(f"SKIP idx={idx}: 片单里没有")
    total = sum(os.path.getsize(os.path.join(DEST_DIR, n)) for n in ok)
    log(f"=== 自然风光扩充完成：{len(ok)}/16 成功，新增 {total/1073741824:.2f}GB ===")


if __name__ == "__main__":
    main()
