# -*- coding: utf-8 -*-
"""Safe backend-boundary tests: OS actions are always patched, never executed."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
from preferences import ConfigError


class ConfigIntegrationTests(unittest.TestCase):
    def test_fresh_update_preserves_external_and_unknown_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            initial = {"video": "original.mov", "mpv": "keep.exe", "future": {"keep": True}}
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(initial, stream)
            with patch.object(app, "CONFIG", path):
                snapshot = app.load_cfg()
                initial["burst_seconds"] = 15
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump(initial, stream)
                updated = app.update_cfg(always_play=True)
                self.assertNotIn("burst_seconds", snapshot)
                self.assertEqual(15, updated["burst_seconds"])
                self.assertEqual({"keep": True}, updated["future"])
                self.assertEqual("keep.exe", updated["mpv"])
                self.assertTrue(app.load_cfg()["always_play"])

    def test_corrupt_config_cannot_be_overwritten_by_an_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "wb") as stream:
                stream.write(b'{"broken":')
            with patch.object(app, "CONFIG", path):
                with self.assertRaises(ConfigError):
                    app.update_cfg(burst_seconds=8)
            with open(path, "rb") as stream:
                self.assertEqual(b'{"broken":', stream.read())


class PowerShellBoundaryTests(unittest.TestCase):
    def test_success_requires_zero_exit_code(self):
        result = SimpleNamespace(returncode=1, stdout="ok\nYES\n", stderr="denied")
        with patch.object(app.subprocess, "run", return_value=result) as run:
            output = app.ps("test-only")
        self.assertIn("ERR PowerShell exited 1", output)
        self.assertEqual("test-only", run.call_args.args[0][-1])

    def test_task_check_does_not_treat_failure_as_disabled(self):
        for text, expected in (("YES\n", True), ("NO\n", False), ("banner\n YES \n", True)):
            with self.subTest(text=text), patch.object(app, "ps", return_value=text):
                self.assertIs(expected, app.task_installed())
        for text in ("", "ERR denied", "YES\nERR PowerShell exited 1"):
            with self.subTest(text=text), patch.object(app, "ps", return_value=text):
                with self.assertRaises(RuntimeError):
                    app.task_installed()

    def test_startup_mutations_require_exact_success_token(self):
        for function in (app.install_task, app.uninstall_task):
            for text, expected in (("ok\n", True), ("Token error", False), ("not ok", False),
                                   ("ok\nERR exit 1", False), ("", False)):
                with self.subTest(function=function.__name__, text=text), patch.object(app, "ps", return_value=text):
                    self.assertIs(expected, function())

    def test_startup_quotes_paths_and_names_with_apostrophes(self):
        with patch.object(app, "_task_cmdlines", return_value=("C:\\O'Neil\\app.exe", "--run")), \
                patch.object(app, "APP_DIR", "C:\\O'Neil"), \
                patch.object(app, "TASK", "Aerial's task"), \
                patch.object(app, "ps", return_value="ok") as ps:
            self.assertTrue(app.install_task())
        command = ps.call_args.args[0]
        self.assertIn("'C:\\O''Neil\\app.exe'", command)
        self.assertIn("'C:\\O''Neil'", command)
        self.assertIn("'Aerial''s task'", command)


class StartBoundaryTests(unittest.TestCase):
    def test_source_start_captures_startup_errors_without_a_console(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(app, "APP_DIR", directory), \
                patch.object(app, "is_running", return_value=False), \
                patch.object(app, "FROZEN", False), \
                patch.object(app.subprocess, "Popen") as launch:
            self.assertTrue(app.start_player())
            argv = launch.call_args.args[0]
            options = launch.call_args.kwargs
            self.assertTrue(argv[0].lower().endswith("pythonw.exe"))
            self.assertEqual(argv[-1], os.path.join(directory, "wallpaper_player.py"))
            self.assertEqual(options["cwd"], directory)
            self.assertEqual(options["stdin"], app.subprocess.DEVNULL)
            self.assertEqual(options["stderr"], app.subprocess.STDOUT)
            self.assertEqual(options["stdout"].name, os.path.join(directory, "player-startup.log"))
            self.assertTrue(options["stdout"].closed)

    def test_frozen_start_preserves_the_run_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(app, "APP_DIR", directory), \
                patch.object(app, "is_running", return_value=False), \
                patch.object(app, "FROZEN", True), \
                patch.object(app.subprocess, "Popen") as launch:
            self.assertTrue(app.start_player())
            self.assertEqual(launch.call_args.args[0], [app.sys.executable, "--run"])

    def test_already_running_does_not_spawn_or_open_a_log(self):
        with patch.object(app, "is_running", return_value=True), \
                patch.object(app.subprocess, "Popen") as launch, \
                patch("builtins.open") as opened:
            self.assertTrue(app.start_player())
        launch.assert_not_called()
        opened.assert_not_called()


class StopBoundaryTests(unittest.TestCase):
    def test_normal_event_stop_never_forces_processes_or_touches_wallpaper(self):
        with patch.object(app, "signal_player", return_value=True) as signal, \
                patch.object(app, "wait_stopped", return_value=True), \
                patch.object(app, "is_running", return_value=False), \
                patch.object(app, "ps", side_effect=AssertionError("unexpected process termination")), \
                patch.object(app, "freeze_to_video", side_effect=AssertionError("unexpected wallpaper change")):
            self.assertTrue(app.stop_player())
        signal.assert_called_once_with(app.EVT_STOP)

    def test_fallback_stop_succeeds_even_when_config_image_lookup_fails(self):
        for error in (ConfigError("corrupt config"), OSError("missing image")):
            with self.subTest(error=error), \
                    patch.object(app, "signal_player", return_value=False), \
                    patch.object(app, "wait_stopped", return_value=True), \
                    patch.object(app, "is_running", return_value=False), \
                    patch.object(app, "ps", return_value="") as ps, \
                    patch.object(app, "freeze_to_video", side_effect=error):
                self.assertTrue(app.stop_player())
                command = ps.call_args.args[0]
                self.assertNotIn("Get-Process mpv", command)
                self.assertIn("$_.Name -eq 'mpv.exe' -and $_.CommandLine -like '*aerial_wallpaper_mpv*'", command)
                self.assertIn("$_.Name -in @('python.exe','pythonw.exe')", command)
                self.assertIn("--run", command)

    def test_restore_failure_cannot_report_failed_stop(self):
        with patch.object(app, "signal_player", return_value=False), \
                patch.object(app, "wait_stopped", return_value=True), \
                patch.object(app, "is_running", return_value=False), \
                patch.object(app, "ps", return_value=""), \
                patch.object(app, "freeze_to_video", return_value=False), \
                patch.object(app, "restore_original", side_effect=ConfigError("unreadable config")):
            self.assertTrue(app.stop_player())


if __name__ == "__main__":
    unittest.main(verbosity=2)
