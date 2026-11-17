# -*- coding: utf-8 -*-
"""视频库扩充：新增风景素材（2026-11-17）

从 Apple tvOS13 官方片单挑 16 条库中还没有的风景向视频：
ISS 地球太空视角 12 条 + 纽约夜景 + 南加州海岸 + 加勒比海 2 条。
统一存 X:\动态壁纸视频，命名风格与既有库一致。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import fetch_aerial as fa

DEST_DIR = r"X:\动态壁纸视频"

# (catalog idx, 保存文件名)
PICKS = [
    # ---- ISS 地球太空视角 ----
    (1,  "Africa_Night_1_4K.mov"),            # 非洲之夜（城市灯光）
    (2,  "Africa_MiddleEast_2_4K.mov"),       # 非洲与中东
    (6,  "Atlantic_SpainFrance_6_4K.mov"),    # 大西洋→西班牙法国
    (12, "California_to_Vegas_12_4K.mov"),    # 加州→拉斯维加斯
    (44, "Iran_Afghanistan_44_4K.mov"),       # 伊朗与阿富汗（雪山）
    (45, "Ireland_to_Asia_45_4K.mov"),        # 爱尔兰→亚洲
    (46, "Italy_to_Asia_46_4K.mov"),          # 意大利→亚洲
    (49, "Korea_Japan_Night_49_4K.mov"),      # 朝鲜半岛与日本之夜
    (68, "Nile_Delta_68_4K.mov"),             # 尼罗河三角洲
    (75, "Sahara_Italy_75_4K.mov"),           # 撒哈拉→意大利
    (85, "SouthAfrica_NorthAsia_85_4K.mov"),  # 南非→北亚
    (89, "WestAfrica_Alps_89_4K.mov"),        # 西非→阿尔卑斯
    # ---- 城市夜景 / 海岸 ----
    (66, "New_York_Night_66_4K.mov"),         # 纽约夜景
    (86, "SoCal_Baja_86_4K.mov"),             # 南加州→巴哈海岸线
    (15, "Caribbean_Day_15_4K.mov"),          # 加勒比海（白天）
    (13, "Caribbean_13_4K.mov"),              # 加勒比海
]

LOG = os.path.join(HERE, "expand_videos.log")


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
    log("=== 风景素材扩充开始（16 条）===")
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
    log(f"=== 扩充完成：{len(ok)}/16 成功，新增 {total/1073741824:.2f}GB ===")


if __name__ == "__main__":
    main()
