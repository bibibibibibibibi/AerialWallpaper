# -*- coding: utf-8 -*-
"""视频库恢复：从 Apple 官方服务器重新下载被删除的壁纸视频（2026-11-16）

来源与命名完全对齐删除前的库（见 .workbuddy/memory/2026-11-04.md 与会话记录）。
优先级：正在使用的 China_20 -> 金门 Sonoma 四条 -> 其余。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import fetch_aerial as fa

# 视频统一存到 X 盘（用户指定，2026-11-16）
DEST_DIR = r"X:\动态壁纸视频"

# ---- 金门 Sonoma 组（macOS Sonoma 动态壁纸，特殊 URL）----
SONOMA_BASE = ("https://sylvan.apple.com/itunes-assets/Aerials116/v4/"
               "cb/5b/50/cb5b5035-6701-619f-9065-3d7d0e5fbef4/")
SONOMA = [
    ("GG_A_SUNSET_MarshallsBeach_c28_v7_24comp_HFR_16Mbps.mov",
     "Golden_Gate_Sunset_Sonoma_4K.mov"),
    ("GG_A_DAY_Bluffs_C35_V25_24comp_HFR_16Mbps.mov",
     "Golden_Gate_Day_Sonoma_4K.mov"),
    ("GG_A_EVENING_TorpedoWharf_C35_v22_24comp_HFR_16Mbps.mov",
     "Golden_Gate_Evening_Sonoma_4K.mov"),
    ("GG_A_NIGHT_BatterySpencer_c9_v8_Final01_24comp_HFR_12Mbps.mov",
     "Golden_Gate_Night_Sonoma_4K.mov"),
]

# ---- 2018 片单里自定义命名的几条 ----
CUSTOM = [
    ("https://sylvan.apple.com/Aerials/2x/Videos/comp_A006_C003_1219EE_CC_v01_SDR_PS_FINAL_20180709_SDR_4K_HEVC.mov",
     "Golden_Gate_A006_4K.mov"),
    ("https://sylvan.apple.com/Aerials/2x/Videos/comp_A008_C007_011550_CC_v01_SDR_PS_FINAL_20180709_SDR_4K_HEVC.mov",
     "Golden_Gate_A008_4K.mov"),
    ("https://sylvan.apple.com/Aerials/2x/Videos/comp_A013_C012_0122D6_CC_v01_SDR_PS_FINAL_20180709_SDR_4K_HEVC.mov",
     "Golden_Gate_A013_4K.mov"),
    ("https://sylvan.apple.com/Aerials/2x/Videos/comp_1223LV_FLARE_v21_SDR_PS_FINAL_20180709_F0F5700_SDR_4K_HEVC.mov",
     "Golden_Gate_A076_4K.mov"),
    ("https://sylvan.apple.com/Aerials/2x/Videos/HK_H004_C013_4K_SDR_HEVC.mov",
     "HongKong_4K.mov"),
]

# ---- 片单编号下载（名字里的数字就是 idx）----
CATALOG_IDX = [
    (20, "China_20_4K.mov"),          # 删除前正在使用的壁纸，最优先
    (73, "Palau_Jellies_73_4K.mov"),
    (24, "Cownose_Rays_24_4K.mov"),
    (87, "Tahiti_Waves_87_4K.mov"),
    (43, "Humpback_Whale_43_4K.mov"),
    (69, "North_America_Aurora_69_4K.mov"),
    (32, "Greenland_32_4K.mov"),
    (5,  "Antarctica_5_4K.mov"),
    (7,  "Australia_7_4K.mov"),
    (37, "Hawaii_37_4K.mov"),
    (51, "Liwa_51_4K.mov"),
    (67, "New_Zealand_67_4K.mov"),
]

LOG = os.path.join(HERE, "restore_videos.log")


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
    log("=== 视频库恢复开始 ===")
    # 1. 正在用的壁纸
    items = {x["idx"]: x for x in fa.load_catalog()}
    for idx, name in CATALOG_IDX:
        it = items.get(idx)
        if it:
            fetch(it["u4k"], name)
        else:
            log(f"SKIP idx={idx}: 片单里没有")
    # 2. 金门 Sonoma 组
    for fn, name in SONOMA:
        fetch(SONOMA_BASE + fn, name)
    # 3. 其余自定义
    for url, name in CUSTOM:
        fetch(url, name)
    # 4. 汇总
    total = 0
    ok = []
    for _, name in CATALOG_IDX:
        p = os.path.join(DEST_DIR, name)
        if os.path.exists(p):
            ok.append(name)
            total += os.path.getsize(p)
    for _, name in SONOMA + CUSTOM:
        p = os.path.join(DEST_DIR, name)
        if os.path.exists(p):
            ok.append(name)
            total += os.path.getsize(p)
    log(f"=== 恢复完成：{len(ok)} 个文件，共 {total/1073741824:.2f}GB ===")
    log("缺失: " + ",".join(
        n for _, n in CATALOG_IDX if n not in ok) or "无")


if __name__ == "__main__":
    main()
