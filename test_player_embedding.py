# -*- coding: utf-8 -*-
"""Offline embedding regressions; never create windows, launch mpv, or write settings.

Run: python -B -m unittest test_player_embedding -v
Every DLL is mocked BEFORE importing the player (including its DPI setup).
"""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


def load_player():
    path = Path(__file__).with_name("wallpaper_player.py")
    spec = importlib.util.spec_from_file_location("_embedding_test_player", path)
    module = importlib.util.module_from_spec(spec)
    # Do not import the normally cached production module: its DLL handles may
    # already be live when a larger test suite imports this regression module.
    with patch.object(ctypes, "WinDLL", create=True,
                      side_effect=lambda *args, **kwargs: MagicMock()):
        spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        # A tiny increment also bounds accidental busy loops without real waits.
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise AssertionError("negative sleep")
        self.sleeps.append(seconds)
        self.now += seconds

    def wall_clock(self):
        raise AssertionError("readiness must use monotonic time")


class PlayerEmbeddingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.player = load_player()

    def test_command_only_uses_embedding_capable_video_outputs(self):
        argv = self.player.mpv_command("mpv.exe", "movie.mov", 1180894)
        outputs = [option.split("=", 1)[1] for option in argv
                   if option.startswith("--vo=")]
        self.assertEqual(outputs, ["gpu-next,gpu,direct3d"])
        self.assertNotIn("", outputs[0].split(","), "empty entry enables automatic fallback")
        self.assertNotIn("sdl", outputs[0].split(","))
        self.assertIn("--wid=1180894", argv)

    def test_command_isolates_configuration_and_keeps_renderer_log(self):
        argv = self.player.mpv_command("mpv.exe", "movie.mov", 42)
        self.assertIn("--no-config", argv)
        self.assertIn("--load-scripts=no", argv)
        logs = [option for option in argv if option.startswith("--log-file=")]
        self.assertEqual(logs, ["--log-file=" + os.path.join(self.player.HERE, "mpv.log")])

    def test_command_keeps_paths_options_and_matching_ipc_pipe(self):
        executable = r"C:\Program Files\mpv\mpv.exe"
        video = r"X:\动态壁纸视频\China 18.mov"
        argv = self.player.mpv_command(executable, video, 42,
                                       start_options=("--start=12.50",))
        self.assertEqual(argv[0], executable)
        self.assertEqual(argv[-2:], ["--", video])
        self.assertIn("--start=12.50", argv[:-2])
        self.assertEqual(argv.count("--start=12.50"), 1)
        pipes = [option for option in argv if option.startswith("--input-ipc-server=")]
        self.assertEqual(self.player.PIPE_NAME, "aerial_wallpaper_mpv")
        self.assertEqual(pipes, ["--input-ipc-server=" + self.player.PIPE_NAME])
        for option in ("--loop=inf", "--no-audio", "--no-stop-screensaver",
                       "--hwdec=auto-safe", "--no-osd-bar", "--osd-level=0",
                       "--video-unscaled=no", "--keepaspect=yes", "--panscan=0.0",
                       "--no-input-default-bindings", "--no-osc", "--border=no"):
            with self.subTest(option=option):
                self.assertIn(option, argv)

    def make_host_with_mock_parent(self, actual_parent, show=False):
        """Exercise the real factory with every integration boundary replaced."""
        native = MagicMock()
        native.CreateWindowExW.return_value = 42
        native.GetWindowLongW.return_value = self.player.WS_POPUP
        native.SetParent.return_value = 0  # NULL is also success for an old NULL parent.
        native.GetParent.return_value = actual_parent
        kernel = MagicMock()
        kernel.GetModuleHandleW.return_value = 123
        notifier = MagicMock()
        logger = MagicMock()
        with ExitStack() as patches:
            patches.enter_context(patch.object(self.player, "u", native))
            patches.enter_context(patch.object(self.player, "k32", kernel))
            patches.enter_context(patch.object(self.player, "get_wallpaper_layer", return_value=77))
            patches.enter_context(patch.object(self.player, "screen_size", return_value=(640, 360)))
            patches.enter_context(patch.object(self.player, "ensure_notify_window", notifier))
            patches.enter_context(patch.object(self.player, "log", logger))
            result = self.player.make_wallpaper_window(show=show)
        return result, native, notifier, logger

    def test_host_converts_style_before_parenting_and_accepts_null_old_parent(self):
        result, native, notifier, logger = self.make_host_with_mock_parent(77)
        self.assertEqual(result, 42)
        native.SetParent.assert_called_once_with(42, 77)
        native.GetParent.assert_called_once_with(42)
        style_call = native.SetWindowLongW.call_args
        self.assertEqual(style_call.args[:2], (42, self.player.GWL_STYLE))
        style = style_call.args[2]
        self.assertFalse(style & self.player.WS_POPUP)
        self.assertTrue(style & self.player.WS_CHILD)
        self.assertTrue(style & self.player.WS_CLIPSIBLINGS)
        calls = [item[0] for item in native.mock_calls]
        self.assertLess(calls.index("SetWindowLongW"), calls.index("SetParent"))
        self.assertLess(calls.index("SetParent"), calls.index("GetParent"))
        native.DestroyWindow.assert_not_called()
        notifier.assert_called_once_with()

    def test_host_parent_mismatch_destroys_owned_window_without_show_or_notifier(self):
        result, native, notifier, logger = self.make_host_with_mock_parent(65548, show=True)
        self.assertIsNone(result)
        native.GetParent.assert_called_once_with(42)
        native.DestroyWindow.assert_called_once_with(42)
        native.ShowWindow.assert_not_called()
        native.SetWindowPos.assert_not_called()
        notifier.assert_not_called()
        logger.assert_called()

    def readiness_environment(self, children, owners=None, messages=0):
        """Supply descendant batches and a finite fake native message queue."""
        clock = FakeClock()
        native = MagicMock()
        native.IsWindow.return_value = True
        native.PeekMessageW.side_effect = [1] * messages + [0] * 500
        owners = owners or {}
        batches = iter(children)
        last = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, self.player.H,
                                          wintypes.LPARAM)

        def enumerate_children(hwnd, callback, context):
            nonlocal last
            last = next(batches, last)
            callback = ctypes.cast(callback, callback_type)
            for child in last:
                if not callback(child, context):
                    break
            return True

        def get_pid(hwnd, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = owners.get(hwnd, 0)
            return 123

        native.EnumChildWindows.side_effect = enumerate_children
        native.GetWindowThreadProcessId.side_effect = get_pid
        fake_time = SimpleNamespace(monotonic=clock.monotonic,
                                    sleep=clock.sleep, time=clock.wall_clock)
        return native, clock, fake_time

    def test_readiness_pumps_messages_and_accepts_matching_descendant(self):
        native, clock, fake_time = self.readiness_environment([[222]], {222: 9001}, messages=2)
        process = SimpleNamespace(pid=9001, poll=MagicMock(return_value=None))
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            ready = self.player.wait_mpv_ready(42, timeout=1.0, settle=0, process=process)
        self.assertTrue(ready)
        self.assertGreaterEqual(native.PeekMessageW.call_count, 3)
        self.assertEqual(native.TranslateMessage.call_count, 2)
        self.assertEqual(native.DispatchMessageW.call_count, 2)
        process.poll.assert_called()

    def test_readiness_rejects_other_process_children_until_mpv_arrives(self):
        native, clock, fake_time = self.readiness_environment([[111], [111, 222]],
                                                             {111: 8000, 222: 9001})
        process = SimpleNamespace(pid=9001, poll=MagicMock(return_value=None))
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            ready = self.player.wait_mpv_ready(42, timeout=1.0, settle=0, process=process)
        self.assertTrue(ready)
        self.assertGreaterEqual(native.EnumChildWindows.call_count, 2)
        self.assertTrue(clock.sleeps)

    def test_readiness_times_out_for_only_unrelated_children(self):
        native, clock, fake_time = self.readiness_environment([[111]], {111: 8000})
        process = SimpleNamespace(pid=9001, poll=MagicMock(return_value=None))
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            ready = self.player.wait_mpv_ready(42, timeout=0.15, settle=0, process=process)
        self.assertFalse(ready)
        self.assertLess(clock.now, 101.0)
        native.GetWindowThreadProcessId.assert_called()

    def test_readiness_returns_immediately_when_mpv_exited(self):
        native, clock, fake_time = self.readiness_environment([[222]], {222: 9001})
        process = SimpleNamespace(pid=9001, poll=MagicMock(return_value=1))
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            ready = self.player.wait_mpv_ready(42, timeout=8.0, settle=0, process=process)
        self.assertFalse(ready)
        self.assertFalse(clock.sleeps)
        self.assertLess(clock.now, 100.1)

    def test_readiness_times_out_without_any_embedded_child(self):
        native, clock, fake_time = self.readiness_environment([[]])
        process = SimpleNamespace(pid=9001, poll=MagicMock(return_value=None))
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            self.assertFalse(self.player.wait_mpv_ready(42, timeout=0.15, settle=0,
                                                       process=process))
        self.assertTrue(clock.sleeps)
        self.assertLess(clock.now, 101.0)

    def test_readiness_without_process_retains_child_window_compatibility(self):
        native, clock, fake_time = self.readiness_environment([[222]])
        with patch.object(self.player, "u", native), patch.object(self.player, "time", fake_time):
            self.assertTrue(self.player.wait_mpv_ready(42, timeout=1.0, settle=0))


if __name__ == "__main__":
    unittest.main()
