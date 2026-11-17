# -*- coding: utf-8 -*-
"""状态机回归测试：暂停原因按来源独立记录/清除，内部事件不得误解除暂停。

这些用例对应真实修复过的 bug：
  - trigger_burst 无差别清 force_pause -> 熄屏/锁屏/屏保后面偷偷解码
  - 空闲兜底 20 秒太短 -> 正常工作停顿一下就反复播放
  - 屏保退出与「立即动一下」共用事件 -> 暂停状态被一锅端
"""
import sys
import time
import wallpaper_player as wp

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


def reset():
    wp.STATE["blockers"].clear()
    wp.STATE["burst_until"] = 0.0
    wp.STATE["log"].clear()


print("== 1. 三种暂停原因相互独立 ==")
reset()
wp.block("display", True)
wp.block("lock", True)
wp.block("saver", True)
check("三种原因同时在场", wp.STATE["blockers"] == {"display", "lock", "saver"})
wp.block("lock", False)
check("解锁只清 lock", wp.STATE["blockers"] == {"display", "saver"}, str(wp.STATE["blockers"]))
wp.block("display", False)
check("亮屏只清 display", wp.STATE["blockers"] == {"saver"}, str(wp.STATE["blockers"]))
wp.block("saver", False)
check("屏保退出清 saver", not wp.STATE["blockers"])

print("== 2. 内部事件不解除暂停（核心 bug 回归）==")
reset()
wp.block("display", True)
wp.block("saver", True)
for why in ("refresh", "nudge", "switch", "mpv-restart", "host-rebuilt", "start"):
    wp.trigger_burst(10, why)
    check(f"{why} 不解除暂停", wp.STATE["blockers"] == {"display", "saver"},
          str(wp.STATE["blockers"]))

print("== 3. trigger_burst 只延长播放时长 ==")
reset()
t0 = time.time()
wp.trigger_burst(7, "panel")
check("burst 延长约 7 秒", 6.5 <= wp.STATE["burst_until"] - t0 <= 8.0)
check("trigger 不产生暂停原因", not wp.STATE["blockers"])

print("== 4. want_paused 组合逻辑（与主循环同式）==")
def want_paused(always=False, now=None):
    now = now if now is not None else time.time()
    return bool(wp.STATE["blockers"]) or (not always and now >= wp.STATE["burst_until"])

reset()
wp.trigger_burst(10, "start")
check("播放中 -> 不暂停", not want_paused(now=time.time() + 1))
check("播满 10 秒 -> 暂停", want_paused(now=time.time() + 11))
check("一直动忽略到期", not want_paused(always=True, now=time.time() + 11))
wp.block("lock", True)
check("锁屏 -> 立刻暂停(即使一直动)", want_paused(always=True, now=time.time() + 1))

print("== 5. 屏保退出：若已熄屏则继续暂停 ==")
reset()
wp.trigger_burst(10, "start")
wp.block("saver", True)
wp.block("display", True)
wp.block("saver", False)
check("屏保退出后熄屏仍在 -> 保持暂停", want_paused(always=True, now=time.time() + 1))
wp.block("display", False)
wp.trigger_burst(10, "display-on")
check("亮屏后 -> 恢复播放", not want_paused(always=True, now=time.time() + 1))

print(f"\n结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
