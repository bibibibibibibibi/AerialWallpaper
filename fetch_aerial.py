# -*- coding: utf-8 -*-
"""Apple 空中壁纸取片工具（自然类精选）

用法:
  python fetch_aerial.py --list            # 列出自然类候选
  python fetch_aerial.py --list all        # 列出全部 89 条
  python fetch_aerial.py --get 37          # 按编号下载（4K SDR）
  python fetch_aerial.py --get 37 --1080   # 下载 1080p 版（更省空间）
  python fetch_aerial.py --get 37 --apply  # 下载后立即切换为当前壁纸
"""
import argparse
import json
import os
import re
import ssl
import sys
import tarfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
TAR = os.path.join(HERE, 'assets', 'resources-13.tar')
TAR_URL = 'https://sylvan.apple.com/Aerials/resources-13.tar'
CATALOG = os.path.join(HERE, 'aerial_catalog.json')
CACHE = os.path.join(HERE, 'size_cache.json')
THREADS = 12

NATURE = [
    'Seals', 'Humpback Whale', 'Palau Jellies', 'Alaskan Jellies', 'Palau Coral',
    'Red Sea Coral', 'Tahiti Waves', 'Cownose Rays', 'Gray Reef Sharks', 'Sea Stars',
    'Kelp', 'California Kelp Forest', 'California Dolphins', 'Costa Rica Dolphins',
    'Barracuda', 'Jacks', 'Bumpheads', 'North America Aurora', 'Greenland',
    'Antarctica', 'Liwa', 'New Zealand', 'Australia', 'China', 'Hawaii', 'Caribbean',
]

CN = {
    'Seals': '海豹', 'Humpback Whale': '座头鲸', 'Palau Jellies': '帕劳水母湖',
    'Alaskan Jellies': '阿拉斯加水母', 'Palau Coral': '帕劳珊瑚', 'Red Sea Coral': '红海珊瑚',
    'Tahiti Waves': '大溪地海浪', 'Cownose Rays': '牛鼻鲼群', 'Gray Reef Sharks': '灰礁鲨',
    'Sea Stars': '海星', 'Kelp': '海藻林', 'California Kelp Forest': '加州巨藻林',
    'California Dolphins': '加州海豚', 'Costa Rica Dolphins': '哥斯达黎加海豚',
    'Barracuda': '梭鱼风暴', 'Jacks': '鲹鱼群', 'Bumpheads': '浪人鲹',
    'North America Aurora': '北美极光', 'Greenland': '格陵兰冰川', 'Antarctica': '南极',
    'Liwa': '利瓦沙漠', 'New Zealand': '新西兰', 'Australia': '澳洲海岸',
    'China': '中国山水', 'Hawaii': '夏威夷', 'Caribbean': '加勒比海',
}

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def ensure_tar():
    if os.path.exists(TAR):
        return
    os.makedirs(os.path.dirname(TAR), exist_ok=True)
    print('首次运行：下载片单（约 1.6MB）…')
    req = urllib.request.Request(TAR_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r, open(TAR, 'wb') as f:
        f.write(r.read())
    print('片单就绪。')


def load_catalog():
    if os.path.exists(CATALOG):
        with open(CATALOG, 'r', encoding='utf-8') as f:
            return json.load(f)
    ensure_tar()
    tf = tarfile.open(TAR)
    data = json.load(tf.extractfile('entries.json'))
    items = []
    for a in data['assets']:
        fn = a['url-4K-SDR'].split('/')[-1]
        m = re.search(r'(\d+)m(\d+)s', fn)
        dur = f"{m.group(1)}'{m.group(2)}" if m else ''
        items.append({
            'label': a.get('accessibilityLabel', '?'),
            'cn': CN.get(a.get('accessibilityLabel', ''), ''),
            'name': fn,
            'dur': dur,
            'u4k': a['url-4K-SDR'],
            'u2k': a['url-1080-SDR'],
        })
    items.sort(key=lambda x: (x['label'], x['name']))
    for i, it in enumerate(items, 1):
        it['idx'] = i
    with open(CATALOG, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    return items


def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def head_size(url):
    try:
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            return int(r.headers.get('Content-Length', 0))
    except Exception:
        return 0


def cmd_list(all_mode=False):
    items = load_catalog()
    cache = load_cache()
    sel = items if all_mode else [x for x in items if x['label'] in NATURE]

    todo = [x for x in sel if x['name'] not in cache]
    if todo:
        print(f'（正在探测 {len(todo)} 个文件大小…）')
        def probe(it):
            key = it['u4k'] if all_mode else it['u4k']
            return it['name'], head_size(key)
        with ThreadPoolExecutor(16) as ex:
            for name, sz in ex.map(probe, todo):
                cache[name] = sz
        with open(CACHE, 'w', encoding='utf-8') as f:
            json.dump(cache, f)

    print(f"{'#':>3} | {'地点':<10s} | {'英文':<22s} | {'时长':<6s} | {'4K':>7s} | 文件")
    print('-' * 108)
    for it in sel:
        mb = cache.get(it['name'], 0) / 1048576
        mbs = f'{mb:.0f}MB' if mb else '?'
        print(f"{it['idx']:3d} | {it['cn']:<10s} | {it['label']:<22s} | {it['dur']:<6s} | {mbs:>7s} | {it['name']}")
    print(f'\n共 {len(sel)} 条。下载：python fetch_aerial.py --get <编号> [--1080] [--apply]')


def download(url, dest, size):
    tmp = dest + '.part'
    if size <= 0:
        sys.exit('无法获取文件大小，下载中止')
    n = THREADS
    chunk = size // n + 1
    done = [0] * n
    lock = threading.Lock()
    f = open(tmp, 'wb')
    f.truncate(size)
    f.close()
    t0 = time.time()

    def worker(i):
        start = i * chunk
        end = min(start + chunk - 1, size - 1)
        if start > end:
            return
        fh = open(tmp, 'r+b')
        fh.seek(start)
        pos = start
        while pos <= end:
            hi = min(pos + 4 * 1024 * 1024 - 1, end)
            req = urllib.request.Request(url, headers={'Range': f'bytes={pos}-{hi}', 'User-Agent': 'Mozilla/5.0'})
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                        data = r.read()
                    break
                except Exception:
                    if attempt == 4:
                        fh.close()
                        raise
                    time.sleep(1.5)
            fh.write(data)
            pos += len(data)
            with lock:
                done[i] += len(data)

    def reporter():
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
            cur = sum(done)
            el = time.time() - t0
            pct = cur * 100.0 / size
            print(f'\r  {pct:5.1f}%  {cur/1048576:6.0f}/{size/1048576:.0f}MB  {cur/1048576/max(el,0.01):5.1f}MB/s', end='', flush=True)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    rep = threading.Thread(target=reporter, daemon=True)
    rep.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print()
    if os.path.getsize(tmp) != size:
        sys.exit(f'分片不完整：{os.path.getsize(tmp)} / {size}')
    if os.path.exists(dest):
        os.remove(dest)
    os.rename(tmp, dest)


def cmd_get(index, hd1080=False, apply_now=False):
    items = load_catalog()
    hit = [x for x in items if x['idx'] == index]
    if not hit:
        sys.exit(f'编号 {index} 不存在')
    it = hit[0]
    url = it['u2k'] if hd1080 else it['u4k']
    ext = '_1080p.mov' if hd1080 else '_4K.mov'
    safe = re.sub(r'[^\w\-]+', '_', it['label'])
    dest = os.path.join(HERE, f"{safe}_{index}{ext}")
    print(f"[{it['idx']}] {it['cn']} / {it['label']}  ->  {os.path.basename(dest)}")
    size = head_size(url)
    print(f'  源: {url}\n  大小: {size/1048576:.0f}MB  线程: {THREADS}')
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        print('  已存在完整文件，跳过下载')
    else:
        download(url, dest, size)
        print(f'  完成: {dest}')
    if apply_now:
        sys.path.insert(0, HERE)
        import subprocess
        py = sys.executable
        r = subprocess.run([py, os.path.join(HERE, 'switch_video.py'), dest], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', nargs='?', const='nature')
    ap.add_argument('--get', type=int)
    ap.add_argument('--1080', dest='hd1080', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if a.list is not None:
        cmd_list(all_mode=(a.list == 'all'))
    elif a.get:
        cmd_get(a.get, a.hd1080, a.apply)
    else:
        ap.print_help()
