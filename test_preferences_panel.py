# -*- coding: utf-8 -*-
"""Isolated native-control regression tests. NEVER touches live app services.

Unlike test_panel.py, these tests own a uniquely named window and inject only
in-memory integrations. Process launch, registry writes, wallpaper APIs and named
player events are explicitly forbidden. Windows is required; Pillow is needed
only by capture_window(), not by the regression tests themselves.
"""
import copy
import ctypes
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack
from ctypes import wintypes as wt
from unittest.mock import patch

from preferences import ConfigError, same_path

if os.name == "nt":
    import preferences_panel as ui
else:
    ui = None


class FakeServices:
    VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
    EVT_PLAY = "test-only-do-not-signal"

    def __init__(self, directory, video=""):
        self.APP_DIR = directory
        self.cfg = {"video": video, "burst_seconds": 8, "always_play": False,
                    "mpv": "untouched-mpv-path", "future_setting": {"keep": [1, 2]}}
        self.running, self.paused, self.auto = True, True, False
        self.calls, self.fail_update, self.fail_auto, self.fail_read = [], False, False, False
        self.auto_release, self.auto_started, self.auto_finished = threading.Event(), threading.Event(), threading.Event()
        self.auto_release.set()
        self.check_release = threading.Event()
        self.check_release.set()
        self.check_failures = 0
        self.stop_release, self.stop_started = threading.Event(), threading.Event()
        self.stop_release.set()
        self.player_ready = threading.Event()
        self.player_ready.set()
        self.lock = threading.RLock()

    def load_cfg(self):
        if self.fail_read:
            raise ConfigError("test: malformed configuration")
        with self.lock:
            return copy.deepcopy(self.cfg)

    def update_cfg(self, **changes):
        if self.fail_update:
            raise ConfigError("test: disk is read-only")
        if self.fail_read:
            raise ConfigError("test: malformed configuration")
        with self.lock:
            self.calls.append(("save", copy.deepcopy(changes)))
            self.cfg.update(changes)
            return copy.deepcopy(self.cfg)

    def is_running(self):
        return self.running

    def read_status(self):
        return {"paused": self.paused, "ts": time.time() if self.player_ready.is_set() else 0}

    def start_player(self):
        self.calls.append(("start",))
        self.running, self.paused = True, False
        return True

    def stop_player(self):
        self.calls.append(("stop",))
        self.stop_started.set()
        if not self.stop_release.wait(8):
            raise RuntimeError("test stop gate timed out")
        self.running = False
        return True

    def signal_player(self, event):
        self.calls.append(("play", event))
        self.paused = False
        return True

    def task_installed(self):
        if not self.check_release.wait(8):
            raise RuntimeError("test check gate timed out")
        if self.check_failures:
            self.check_failures -= 1
            raise RuntimeError("test startup check failed")
        return self.auto

    def _change_auto(self, want):
        self.calls.append(("auto", want))
        self.auto_started.set()
        try:
            if not self.auto_release.wait(8):
                raise RuntimeError("test startup gate timed out")
            if self.fail_auto:
                return False
            self.auto = want
            return True
        finally:
            self.auto_finished.set()

    def install_task(self):
        return self._change_auto(True)

    def uninstall_task(self):
        return self._change_auto(False)

    def install_screensaver(self):
        self.calls.append(("screensaver",))
        return True, "test-only.scr"


def _forbidden(*args, **kwargs):
    raise AssertionError("A live system-changing API escaped the isolated services")


if ui:
    class _TestPanel(ui.PreferencesPanel):
        TEST_MESSAGE = 0x8002

        def __init__(self, services, ready):
            super().__init__(services, "AerialPreferencesTest_" + uuid.uuid4().hex, ready)
            self.test_calls = queue.Queue()

        def _handle_message(self, hwnd, msg, wp, lp):
            if msg == self.TEST_MESSAGE:
                function, event, result = self.test_calls.get_nowait()
                try:
                    result.append((True, function()))
                except BaseException as exc:
                    result.append((False, exc))
                finally:
                    event.set()
                return 0
            return super()._handle_message(hwnd, msg, wp, lp)


class PanelHarness:
    """Own and clean up one isolated window; all callbacks execute on its thread."""

    def __init__(self, services):
        self.services, self.ready, self.done = services, threading.Event(), threading.Event()
        self.panel, self.thread_error = _TestPanel(services, self.ready), None
        self.stack = ExitStack()

    def __enter__(self):
        import winreg
        for module, name in ((subprocess, "Popen"), (subprocess, "run"),
                             (winreg, "SetValueEx"), (winreg, "DeleteValue"),
                             (winreg, "CreateKey"), (winreg, "CreateKeyEx"),
                             (ui.u, "SystemParametersInfoW"), (ui.k, "OpenEventW"),
                             (ui.k, "SetEvent")):
            self.stack.enter_context(patch.object(module, name, side_effect=_forbidden))

        def run():
            try:
                self.panel.run()
            except BaseException as exc:
                self.thread_error = exc
            finally:
                self.ready.set()
                self.done.set()
        self.thread = threading.Thread(target=run, name="isolated-preferences-ui", daemon=True)
        self.thread.start()
        if not self.ready.wait(8):
            self.close()
            raise AssertionError("Isolated panel did not become ready")
        if self.thread_error:
            self.close()
            raise self.thread_error
        if self.done.is_set() or not self.panel.hwnd:
            self.close()
            raise AssertionError("Isolated panel exited before ready")
        self.wait_for(lambda: not self.panel.jobs)
        return self

    def invoke(self, function, timeout=5):
        if self.done.is_set():
            raise AssertionError("UI thread has already exited: %r" % self.thread_error)
        event, result = threading.Event(), []
        self.panel.test_calls.put((function, event, result))
        ui.u.PostMessageW(self.panel.hwnd, self.panel.TEST_MESSAGE, 0, 0)
        if not event.wait(timeout):
            raise AssertionError("UI thread callback timed out")
        ok, value = result[0]
        if not ok:
            raise value
        return value

    def wait_for(self, predicate, timeout=5):
        deadline, tick = time.monotonic() + timeout, threading.Event()
        while time.monotonic() < deadline:
            if self.invoke(predicate):
                return
            tick.wait(.01)
        raise AssertionError("Isolated UI condition did not become true")

    def idle(self):
        self.wait_for(lambda: not self.panel.jobs)

    def text(self, key, value=None):
        if value is None:
            return self.invoke(lambda: ui.get_text(self.panel.controls[key]))
        self.invoke(lambda: ui.u.SetWindowTextW(self.panel.controls[key], str(value)))

    def click(self, key):
        self.invoke(lambda: ui.u.SendMessageW(self.panel.controls[key], ui.BM_CLICK, 0, 0))

    def enabled(self, key):
        return self.invoke(lambda: bool(ui.u.IsWindowEnabled(self.panel.controls[key])))

    def focus(self, key):
        self.invoke(lambda: ui.u.SetFocus(self.panel.controls[key]))

    def key(self, key, virtual_key):
        hwnd = self.panel.controls[key]
        ui.u.PostMessageW(hwnd, ui.WM_KEYDOWN, virtual_key, 1)
        ui.u.PostMessageW(hwnd, 0x101, virtual_key, 0xC0000001)
        self.invoke(lambda: None)

    def close(self):
        if self.panel.hwnd and not self.done.is_set():
            ui.u.PostMessageW(self.panel.hwnd, ui.WM_CLOSE, 0, 0)
            if not self.done.wait(5):
                raise AssertionError("Isolated UI did not close")
        self.services.auto_release.set()
        self.services.check_release.set()
        self.services.stop_release.set()
        self.services.player_ready.set()
        self.stack.close()

    def __exit__(self, *exc):
        self.close()
        if self.thread_error and not exc[0]:
            raise self.thread_error


def capture_window(hwnd, destination):
    """Capture the owned HWND using correctly sized Win64 GDI handles."""
    from PIL import Image

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                    ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                    ("image_size", wt.DWORD), ("xppm", wt.LONG), ("yppm", wt.LONG),
                    ("colors", wt.DWORD), ("important", wt.DWORD)]

    ui._api(ui.u, "GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
    ui._api(ui.u, "PrintWindow", wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
    redraw = ui._api(ui.u, "RedrawWindow", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT),
                     wt.HRGN, wt.UINT)
    redraw(hwnd, None, None, 0x185)  # invalidate/erase/all children, update now
    ui._api(ui.g, "GetDIBits", ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
            ctypes.c_void_p, ctypes.c_void_p, wt.UINT)
    rc = wt.RECT()
    if not ui.u.GetWindowRect(hwnd, ctypes.byref(rc)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = rc.right - rc.left, rc.bottom - rc.top
    if width < 2 or height < 2:
        raise AssertionError("Invalid window capture dimensions")
    screen = ui.u.GetDC(None)
    memory = bitmap = old = None
    try:
        memory = ui.g.CreateCompatibleDC(screen)
        bitmap = ui.g.CreateCompatibleBitmap(screen, width, height)
        if not memory or not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        old = ui.g.SelectObject(memory, bitmap)
        # Use the native WM_PRINT path: PW_RENDERFULLCONTENT (2) can return
        # stale DWM-cached owner-drawn children immediately after DPI relayout.
        if not ui.u.PrintWindow(hwnd, memory, 0):
            raise AssertionError("PrintWindow failed for owned test window")
        ui.g.SelectObject(memory, old)
        old = None
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if ui.g.GetDIBits(screen, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            raise AssertionError("GetDIBits did not return the complete image")
        Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1).save(destination)
    finally:
        if old:
            ui.g.SelectObject(memory, old)
        if bitmap:
            ui.g.DeleteObject(bitmap)
        if memory:
            ui.g.DeleteDC(memory)
        ui.u.ReleaseDC(None, screen)


@unittest.skipUnless(os.name == "nt", "Native preferences controls require Windows")
class NativePreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="preferences-test-", dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(self.temp.cleanup)
        names = ("Alpine_Dawn_4K.mov", "Coastline_04_4K.mov", "Forest_Morning.mp4",
                 "Palau_Coral_70_4K.mov", "Reef_Overhead.webm", "夜色_城市.mkv")
        for name in names:
            with open(os.path.join(self.temp.name, name), "wb"):
                pass
        self.services = FakeServices(self.temp.name, os.path.join(self.temp.name, names[0]))
        self.h = PanelHarness(self.services).__enter__()
        self.addCleanup(self.h.close)
        self.p = self.h.panel

    def tearDown(self):
        self.assertEqual([], self.p.errors, "Unexpected Win32 callback error")

    def test_owned_window_and_stable_ids(self):
        self.assertEqual(ui.ID_BURST, ui.u.GetDlgCtrlID(self.p.controls["burst"]))
        self.assertEqual(6, len(self.p.items))
        work = self.h.invoke(self.p._work_area)
        self.assertLessEqual(self.p.width, work.right - work.left)
        self.assertLessEqual(self.p.height, work.bottom - work.top)

    def test_scenery_buttons_filter_without_changing_playback(self):
        before = copy.deepcopy(self.services.cfg)
        self.assertEqual("全部 6", self.h.text("category_all"))
        self.assertEqual("海洋 3", self.h.text("category_ocean"))
        self.h.click("category_ocean")
        self.assertEqual("ocean", self.p.category)
        self.assertEqual(3, len(self.p.visible))
        self.assertTrue(all(ui.classify_video(self.p.items[i][1]) == "ocean" for i in self.p.visible))
        self.assertEqual(1, self.h.invoke(lambda: ui.u.SendMessageW(self.p.controls["category_ocean"], ui.BM_GETCHECK, 0, 0)))
        self.assertEqual(0, self.h.invoke(lambda: ui.u.SendMessageW(self.p.controls["category_all"], ui.BM_GETCHECK, 0, 0)))
        self.assertEqual(before, self.services.cfg)
        self.assertEqual([], self.services.calls)

    def test_scenery_and_search_combine_and_apply_original_file(self):
        self.h.click("category_ocean")
        self.h.text("search", "coral 4K")
        self.assertEqual(1, len(self.p.visible))
        expected = self.p.selected_item()[1]
        self.assertIn("Palau", expected)
        self.h.click("category_cities")
        self.assertEqual([], self.p.visible)
        self.assertEqual("coral 4K", self.h.text("search"))
        self.h.click("category_ocean")
        self.assertTrue(same_path(expected, self.p.selected_item()[1]))
        self.assertNotEqual(0, self.p.visible[0])
        self.h.click("apply")
        self.assertTrue(same_path(expected, self.services.cfg["video"]))

    def test_empty_category_clears_preview_and_all_restores_library(self):
        before = copy.deepcopy(self.services.cfg)
        self.h.click("category_space")
        self.assertEqual([], self.p.visible)
        self.assertIsNone(self.p.selected_item())
        self.assertFalse(self.h.enabled("apply"))
        self.assertFalse(self.h.invoke(lambda: bool(ui.u.IsWindowVisible(self.p.controls["library"]))))
        self.h.click("category_all")
        self.assertEqual(6, len(self.p.visible))
        self.assertEqual(before, self.services.cfg)

    def test_rescan_preserves_category_search_and_updates_counts(self):
        self.h.click("category_ocean")
        self.h.text("search", "coral")
        expected = self.p.selected_item()[1]
        with open(os.path.join(self.temp.name, "Ocean_New.mov"), "wb"):
            pass
        self.h.click("refresh")
        self.h.idle()
        self.assertEqual("ocean", self.p.category)
        self.assertEqual("coral", self.h.text("search"))
        self.assertEqual("全部 7", self.h.text("category_all"))
        self.assertEqual("海洋 4", self.h.text("category_ocean"))
        self.assertEqual(1, len(self.p.visible))
        self.assertTrue(same_path(expected, self.p.selected_item()[1]))
        self.assertEqual([], self.services.calls)

    def test_scenery_keyboard_activation_is_preview_only(self):
        self.h.focus("category_cities")
        self.h.key("category_cities", 0x20)
        self.assertEqual("cities", self.p.category)
        self.assertEqual(1, len(self.p.visible))
        self.h.key("category_cities", 0x9)
        self.assertEqual(self.p.controls["category_desert"], self.h.invoke(ui.u.GetFocus))
        self.assertEqual([], self.services.calls)

    def test_browse_resets_category_so_selected_file_is_visible(self):
        self.h.click("category_ocean")
        target = os.path.join(self.temp.name, "Alpine_Dawn_4K.mov")
        with patch.object(ui, "choose_video", return_value=target):
            self.h.click("browse")
        self.h.idle()
        self.assertEqual("all", self.p.category)
        self.assertEqual(6, len(self.p.visible))
        self.assertTrue(same_path(target, self.p.selected_item()[1]))
        self.assertEqual("全部 6", self.h.text("category_all"))

    def test_search_is_preview_only_and_maps_original_indexes(self):
        before = copy.deepcopy(self.services.cfg)
        self.h.text("search", "coral 4K")
        self.assertEqual(1, len(self.p.visible))
        self.assertIn("Palau", self.p.selected_item()[0])
        self.assertEqual(before, self.services.cfg)
        expected = self.p.selected_item()[1]
        self.h.click("apply")
        self.assertTrue(same_path(expected, self.services.cfg["video"]))
        self.assertNotEqual(0, self.p.visible[0])

    def test_empty_search_result_disables_apply_and_clears_preview(self):
        self.h.text("search", "no-such-landscape")
        self.assertEqual([], self.p.visible)
        self.assertIsNone(self.p.selected_item())
        self.assertFalse(self.h.enabled("apply"))
        self.assertFalse(self.h.invoke(lambda: bool(ui.u.IsWindowVisible(self.p.controls["library"]))))
        self.h.text("search", "")
        self.assertEqual(6, len(self.p.visible))

    def test_list_selection_does_not_apply(self):
        before = self.services.cfg["video"]
        def select():
            ui.u.SendMessageW(self.p.controls["library"], ui.LB_SETCURSEL, 2, 0)
            ui.u.SendMessageW(self.p.hwnd, ui.WM_COMMAND, ui.ID_LIBLIST | (1 << 16), self.p.controls["library"])
        self.h.invoke(select)
        self.assertNotEqual(before, self.p.selected_item()[1])
        self.assertEqual(before, self.services.cfg["video"])

    def test_dirty_duration_survives_timer_refresh(self):
        self.h.text("burst", "15")
        self.h.invoke(lambda: ui.u.SendMessageW(self.p.hwnd, ui.WM_TIMER, 1, 0))
        self.assertTrue(self.p.dirty)
        self.assertEqual("15", self.h.text("burst"))
        self.assertEqual(8, self.services.cfg["burst_seconds"])

    def test_save_preserves_unknown_config_and_clears_dirty(self):
        self.h.text("burst", "15")
        self.h.click("save")
        self.assertEqual(15, self.services.cfg["burst_seconds"])
        self.assertEqual({"keep": [1, 2]}, self.services.cfg["future_setting"])
        self.assertFalse(self.p.dirty)
        self.assertFalse(self.h.enabled("save"))
        self.assertIn("下一轮", self.p.flash_message)

    def test_blank_and_out_of_range_are_not_silently_saved(self):
        for invalid in ("", "1", "99"):
            self.h.text("burst", invalid)
            self.h.click("save")
            self.assertEqual(8, self.services.cfg["burst_seconds"])
            self.assertTrue(self.p.flash_error)
            self.assertEqual(invalid, self.h.text("burst"))

    def test_save_failure_preserves_draft_and_reports_failure(self):
        self.services.fail_update = True
        self.h.text("burst", "15")
        self.h.click("save")
        self.assertEqual(8, self.services.cfg["burst_seconds"])
        self.assertEqual("15", self.h.text("burst"))
        self.assertTrue(self.p.dirty)
        self.assertTrue(self.p.flash_error)
        self.assertIn("未保存", self.p.flash_message)

    def test_external_config_updates_only_clean_input(self):
        self.services.cfg["burst_seconds"] = 15
        self.h.invoke(self.p.refresh)
        self.assertEqual("15", self.h.text("burst"))
        self.h.text("burst", "20")
        self.services.cfg["burst_seconds"] = 30
        self.h.invoke(self.p.refresh)
        self.assertEqual("20", self.h.text("burst"))
        self.assertTrue(self.p.dirty)

    def test_preset_is_saved_immediately(self):
        self.h.click("preset_30")
        self.assertEqual(30, self.services.cfg["burst_seconds"])
        self.assertEqual("30", self.h.text("burst"))
        self.assertFalse(self.p.dirty)

    def test_stepper_is_draft_until_enter(self):
        self.h.click("more")
        self.assertEqual("9", self.h.text("burst"))
        self.assertEqual(8, self.services.cfg["burst_seconds"])
        self.h.focus("burst")
        self.h.key("burst", 0xD)
        self.assertEqual(9, self.services.cfg["burst_seconds"])
        self.assertFalse(self.p.dirty)

    def test_continuous_mode_disables_burst_controls_without_losing_value(self):
        self.h.click("always")
        self.assertTrue(self.services.cfg["always_play"])
        self.assertFalse(self.h.enabled("burst"))
        self.assertFalse(self.h.enabled("preset_5"))
        self.assertEqual(8, self.services.cfg["burst_seconds"])
        self.h.click("always")
        self.assertFalse(self.services.cfg["always_play"])
        self.assertTrue(self.h.enabled("burst"))

    def test_space_toggles_once_and_tab_moves_focus(self):
        self.h.focus("always")
        self.h.key("always", 0x20)
        self.assertTrue(self.services.cfg["always_play"])
        self.assertEqual(1, len([c for c in self.services.calls if c[0] == "save"]))
        self.h.key("always", 0x9)
        focus = self.h.invoke(ui.u.GetFocus)
        self.assertNotEqual(self.p.controls["always"], focus)
        self.assertIn(focus, self.p.controls.values())

    def test_mouse_release_without_press_and_drag_outside_do_not_toggle(self):
        def mouse_messages():
            hwnd = self.p.controls["always"]
            ui.u.SendMessageW(hwnd, 0x202, 0, 10 | (10 << 16))
            ui.u.SendMessageW(hwnd, 0x201, 1, 10 | (10 << 16))
            ui.u.SendMessageW(hwnd, ui.WM_MOUSEMOVE, 1, 200 | (200 << 16))
            ui.u.SendMessageW(hwnd, 0x202, 0, 200 | (200 << 16))
        self.h.invoke(mouse_messages)
        self.assertFalse(self.services.cfg["always_play"])
        self.assertEqual([], self.services.calls)

    def test_startup_requests_are_serialized(self):
        self.services.auto_release.clear()
        self.h.click("auto")
        self.assertTrue(self.services.auto_started.wait(2))
        self.assertFalse(self.h.enabled("auto"))
        for _ in range(4):
            self.h.click("auto")
        self.assertEqual(1, len([c for c in self.services.calls if c[0] == "auto"]))
        self.services.auto_release.set()
        self.h.idle()
        self.assertTrue(self.p.auto_actual)
        self.assertTrue(self.h.enabled("auto"))
        self.assertEqual(1, self.h.invoke(lambda: ui.u.SendMessageW(self.p.controls["auto"], ui.BM_GETCHECK, 0, 0)))

    def test_startup_failure_does_not_claim_success(self):
        self.services.fail_auto = True
        self.h.click("auto")
        self.h.idle()
        self.assertFalse(self.p.auto_actual)
        self.assertFalse(self.services.auto)
        self.assertTrue(self.p.flash_error)
        self.assertTrue(self.h.enabled("auto"))

    def test_closing_during_startup_request_ignores_late_result(self):
        self.services.auto_release.clear()
        self.h.click("auto")
        self.assertTrue(self.services.auto_started.wait(2))
        previous = self.p.auto_actual
        self.h.close()
        self.assertTrue(self.services.auto_finished.wait(2))
        self.assertTrue(self.p.closed)
        self.assertEqual(previous, self.p.auto_actual)
        self.assertFalse(ui.u.IsWindow(self.p.hwnd))

    def test_player_controls_use_only_injected_services(self):
        self.h.click("play")
        self.h.idle()
        self.assertIn(("play", self.services.EVT_PLAY), self.services.calls)
        self.h.click("toggle")
        self.h.idle()
        self.assertFalse(self.services.running)
        self.assertEqual("启动壁纸", self.h.text("toggle"))
        self.h.click("toggle")
        self.h.idle()
        self.assertTrue(self.services.running)
        self.assertIn(("start",), self.services.calls)

    def test_screensaver_action_is_injected(self):
        self.h.click("install")
        self.h.idle()
        self.assertEqual([("screensaver",)], self.services.calls)
        self.assertIn("屏保已安装", self.p.flash_message)

    def test_config_failure_does_not_overwrite_with_defaults(self):
        self.services.fail_read = True
        before = copy.deepcopy(self.services.cfg)
        self.h.invoke(self.p.refresh)
        self.assertIn("未覆盖原文件", self.p.config_error)
        self.h.text("burst", "15")
        self.h.click("save")
        self.assertEqual(before, self.services.cfg)
        self.assertTrue(self.p.dirty)

    def test_search_escape_clears_and_enter_only_focuses_list(self):
        self.h.text("search", "coral")
        before = copy.deepcopy(self.services.cfg)
        self.h.focus("search")
        self.h.key("search", 0xD)
        self.assertEqual(self.p.controls["library"], self.h.invoke(ui.u.GetFocus))
        self.assertEqual(before, self.services.cfg)
        self.h.focus("search")
        self.h.key("search", 0x1B)
        self.assertEqual("", self.h.text("search"))
        self.assertEqual(6, len(self.p.visible))
        self.assertFalse(self.p.closed)

    def test_ctrl_f_focuses_search(self):
        self.h.focus("burst")
        msg = wt.MSG()
        msg.message, msg.wParam = ui.WM_KEYDOWN, ord("F")
        with patch.object(ui.u, "GetKeyState", return_value=-32768):
            self.h.invoke(lambda: self.p._keyboard(msg))
        self.assertEqual(self.p.controls["search"], self.h.invoke(ui.u.GetFocus))

    def test_dpi_relayout_updates_control_geometry(self):
        self.h.invoke(lambda: self.p._layout(2.0))
        ui._api(ui.u, "GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
        rc = wt.RECT()
        ui.u.GetWindowRect(self.p.controls["save"], ctypes.byref(rc))
        self.assertLessEqual(abs((rc.right - rc.left) - self.p.px(192)), 1)
        self.assertLessEqual(self.p.width, self.h.invoke(self.p._work_area).right - self.h.invoke(self.p._work_area).left)

    def test_dpi_decrease_keeps_library_rows_whole(self):
        ui._api(ui.u, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
        for scale in (2.0, 1.5, 1.0):
            with self.subTest(scale=scale):
                self.h.invoke(lambda: self.p._layout(scale))
                rc = wt.RECT()
                ui.u.GetClientRect(self.p.controls["library"], ctypes.byref(rc))
                row_height = ui.u.SendMessageW(self.p.controls["library"], 0x1A1, 0, 0)
                self.assertGreater(row_height, 0)
                self.assertGreater(rc.bottom - rc.top, 0)
                self.assertEqual(0, (rc.bottom - rc.top) % row_height)

    def test_choose_video_applies_and_previews_new_selection_in_same_directory(self):
        chosen = os.path.join(self.temp.name, "Palau_Coral_70_4K.mov")
        self.h.text("burst", "15")
        self.h.text("search", "alpine")
        with patch.object(ui, "choose_video", return_value=chosen) as choose:
            self.h.click("browse")
            self.h.idle()
        self.assertEqual(chosen, self.services.cfg["video"])
        self.assertEqual(chosen, self.h.invoke(self.p.selected_item)[1])
        self.assertEqual("", self.h.text("search"))
        self.assertEqual({"keep": [1, 2]}, self.services.cfg["future_setting"])
        choose.assert_called_once()
        self.assertEqual("15", self.h.text("burst"))
        self.assertTrue(self.p.dirty)
        self.assertEqual(8, self.services.cfg["burst_seconds"])

    def test_picker_error_preserves_configuration_and_draft(self):
        before = copy.deepcopy(self.services.cfg)
        self.h.text("burst", "15")
        with patch.object(ui, "choose_video", side_effect=OSError("test dialog unavailable")):
            self.h.click("browse")
        self.assertEqual(before, self.services.cfg)
        self.assertEqual("15", self.h.text("burst"))
        self.assertTrue(self.p.dirty)
        self.assertTrue(self.p.flash_error)
        self.assertIn("dialog unavailable", self.p.flash_message)

    def test_choose_video_can_switch_library_directory(self):
        with tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
            chosen = os.path.join(directory, "New_Coast_4K.mp4")
            with open(chosen, "wb"):
                pass
            with patch.object(ui, "choose_video", return_value=chosen):
                self.h.click("browse")
                self.h.idle()
            self.assertEqual(1, len(self.p.items))
            self.assertEqual(chosen, self.h.invoke(self.p.selected_item)[1])
            self.assertEqual(chosen, self.services.cfg["video"])

    def test_cancelled_picker_changes_nothing(self):
        before = copy.deepcopy(self.services.cfg)
        with patch.object(ui, "choose_video", return_value=""):
            self.h.click("browse")
        self.assertEqual(before, self.services.cfg)
        self.assertFalse(any(c[0] in ("save", "start") for c in self.services.calls))

    def test_enter_activates_focused_owner_draw_button(self):
        self.h.focus("always")
        self.h.key("always", 0xD)
        self.assertTrue(self.services.cfg["always_play"])
        self.assertEqual(1, len([c for c in self.services.calls if c[0] == "save"]))

    def test_unrelated_commit_cannot_hide_external_duration_change(self):
        self.services.cfg["burst_seconds"] = 15
        self.h.click("always")
        self.assertEqual("15", self.h.text("burst"))
        self.assertFalse(self.p.dirty)
        self.assertEqual(15, self.services.cfg["burst_seconds"])
        self.h.click("always")
        self.assertEqual("15", self.h.text("burst"))

    def test_failed_startup_verification_reconciles_actual_state(self):
        self.services.check_failures = 1
        self.h.click("auto")
        self.h.idle()
        self.assertTrue(self.services.auto)
        self.assertTrue(self.p.auto_actual)
        self.assertTrue(self.p.flash_error)
        self.h.click("auto")
        self.h.idle()
        self.assertFalse(self.p.auto_actual)
        self.assertEqual([("auto", True), ("auto", False)], [c for c in self.services.calls if c[0] == "auto"])

    def test_unknown_startup_state_retries_read_without_changing_task(self):
        self.h.invoke(lambda: setattr(self.p, "auto_actual", None))
        self.h.click("auto")
        self.h.idle()
        self.assertFalse(self.p.auto_actual)
        self.assertFalse(any(c[0] == "auto" for c in self.services.calls))

    def test_stop_cannot_be_raced_by_library_or_always_actions(self):
        self.services.stop_release.clear()
        self.h.click("toggle")
        self.assertTrue(self.services.stop_started.wait(2))
        before = copy.deepcopy(self.services.cfg)
        self.assertFalse(self.h.enabled("always"))
        self.assertFalse(self.h.enabled("apply"))
        self.h.text("search", "coral")
        self.h.focus("library")
        self.h.key("library", 0xD)
        self.h.invoke(lambda: self.p._command(ui.ID_LIBLIST, 2))
        self.h.invoke(lambda: self.p._command(ui.ID_ALWAYS, 0))
        self.assertEqual(before, self.services.cfg)
        self.services.stop_release.set()
        self.h.idle()
        self.assertFalse(self.services.running)
        self.assertTrue(self.h.enabled("always"))

    def test_start_waits_for_fresh_status_not_just_mutex(self):
        self.services.running = False
        self.services.player_ready.clear()
        self.h.invoke(self.p.refresh)
        self.h.click("toggle")
        self.h.wait_for(lambda: self.services.running)
        self.assertIn("player", self.h.invoke(lambda: dict(self.p.jobs)))
        self.assertNotIn("已启动", self.p.flash_message)
        self.assertFalse(self.h.enabled("toggle"))
        self.services.player_ready.set()
        self.h.idle()
        self.assertIn("已启动", self.p.flash_message)
        self.assertTrue(self.h.enabled("toggle"))

    def test_screensaver_failure_is_not_reported_as_installed(self):
        with patch.object(self.services, "install_screensaver", return_value=(False, "test: missing exe")):
            self.h.click("install")
            self.h.idle()
        self.assertTrue(self.p.flash_error)
        self.assertIn("missing exe", self.p.flash_message)
        self.assertNotIn("已安装", self.p.flash_message)

    def test_deleted_video_is_not_written_to_config(self):
        self.h.text("search", "coral")
        before = copy.deepcopy(self.services.cfg)
        with patch("preferences_panel.os.path.isfile", return_value=False):
            self.h.click("apply")
        self.assertEqual(before, self.services.cfg)
        self.assertTrue(self.p.flash_error)
        self.assertIn("移动或删除", self.p.flash_message)

    def test_minimize_and_close_are_real_controls(self):
        ui._api(ui.u, "IsIconic", wt.BOOL, wt.HWND)
        self.h.click("minimize")
        self.assertTrue(ui.u.IsIconic(self.p.hwnd))
        self.h.click("close")
        self.assertTrue(self.h.done.wait(2))
        self.assertTrue(self.p.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
