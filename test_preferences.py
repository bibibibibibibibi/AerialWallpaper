# -*- coding: utf-8 -*-
"""Safe, standard-library-only tests: no application, UI or player imports.

Run from this directory with:
    python -B -m unittest test_preferences -v
"""

import copy
import errno
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest import mock

if __package__:
    from . import preferences as pref
else:
    import preferences as pref


class ConstantsTests(unittest.TestCase):
    def test_public_constants(self):
        self.assertEqual(pref.DEFAULT_BURST, 8)
        self.assertEqual(pref.MIN_BURST, 3)
        self.assertEqual(pref.MAX_BURST, 30)
        self.assertEqual(pref.BURST_PRESETS, (5, 8, 15, 30))
        self.assertEqual((pref.BOARD_WIDTH, pref.BOARD_HEIGHT), (1064, 720))
        self.assertTrue(issubclass(pref.ConfigError, Exception))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aerial-preferences-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "配置 settings.json"

    def assert_only_config_remains(self):
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_missing_file_returns_fresh_empty_dict_without_creating_file(self):
        first = pref.read_config(self.path)
        first["changed"] = True
        self.assertEqual(pref.read_config(self.path), {})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_missing_parent_returns_empty_dict_without_creating_directory(self):
        self.assertEqual(pref.read_config(self.directory / "absent" / "config.json"), {})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_reads_utf8_dictionary_and_unknown_values(self):
        expected = {"video": "影片/上海_夜景.mov", "unknown": {"list": [1, None, True, "海"]}}
        self.path.write_bytes(json.dumps(expected, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(pref.read_config(self.path), expected)
        self.assert_only_config_remains()

    def test_corrupt_json_is_reported_and_never_overwritten(self):
        cases = [b"", b"  ", b"{", b'{"video":', b'{"video": 1,}',
                 b"not json", b"{} {}", b'{"x": NaN}', b'{"x": Infinity}',
                 b'{"x": -Infinity}']
        for content in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaisesRegex(pref.ConfigError, "Cannot read configuration"):
                    pref.read_config(self.path)
                self.assertEqual(self.path.read_bytes(), content)
                self.assert_only_config_remains()

    def test_non_dictionary_roots_are_reported_without_overwrite(self):
        for root in ([], [1], None, True, False, 5, 8.5, "text"):
            with self.subTest(root=root):
                original = json.dumps(root).encode("utf-8")
                self.path.write_bytes(original)
                with self.assertRaisesRegex(pref.ConfigError, "JSON object"):
                    pref.read_config(self.path)
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_only_config_remains()

    def test_invalid_utf8_is_not_a_missing_configuration(self):
        content = b'{"video": "\xff"}'
        self.path.write_bytes(content)
        with self.assertRaises(pref.ConfigError):
            pref.read_config(self.path)
        self.assertEqual(self.path.read_bytes(), content)
        self.assert_only_config_remains()

    def test_permission_and_other_read_errors_raise_config_error(self):
        self.path.write_bytes(b'{"keep": true}')
        for error in (PermissionError("read denied"), OSError("disk unavailable")):
            with self.subTest(error=error):
                with mock.patch("builtins.open", side_effect=error):
                    with self.assertRaises(pref.ConfigError) as caught:
                        pref.read_config(self.path)
                self.assertIs(caught.exception.__cause__, error)
                self.assertIn(str(error), str(caught.exception))
                self.assertEqual(self.path.read_bytes(), b'{"keep": true}')
                self.assert_only_config_remains()

    def test_directory_and_invalid_path_are_not_empty_configurations(self):
        for path in (self.directory, None, "\x00"):
            with self.subTest(path=path):
                with self.assertRaises(pref.ConfigError):
                    pref.read_config(path)

    def test_roundtrip_preserves_all_unknown_keys_without_mutation(self):
        cfg = {
            "burst_seconds": 15,
            "video": "C:\\影片\\城市_夜景.mov",
            "future_setting": {"enabled": True, "levels": [None, 2.5, "海"]},
            "unknown_list": [1, {"unchanged": "value"}],
        }
        expected = copy.deepcopy(cfg)
        nested = cfg["future_setting"]
        pref.write_config(self.path, cfg)
        self.assertEqual(pref.read_config(self.path), expected)
        self.assertEqual(cfg, expected)
        self.assertIs(cfg["future_setting"], nested)
        raw = self.path.read_bytes()
        self.assertIn("城市_夜景".encode("utf-8"), raw)
        self.assertIn(b'\n  "burst_seconds": 15', raw)
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assert_only_config_remains()

    def test_read_edit_write_keeps_unrelated_configuration(self):
        original = {"burst_seconds": "8.0", "future": {"retain": ["所有", 1]}, "always": False}
        self.path.write_bytes(json.dumps(original, ensure_ascii=False).encode("utf-8"))
        cfg = pref.read_config(self.path)
        cfg["burst_seconds"] = pref.validate_burst("15")
        pref.write_config(self.path, cfg)
        expected = dict(original, burst_seconds=15)
        self.assertEqual(pref.read_config(self.path), expected)
        self.assert_only_config_remains()

    def test_write_uses_closed_same_directory_temp_and_atomic_replace(self):
        old = b'{"old": "must stay until replace"}'
        self.path.write_bytes(old)
        cfg = {"new": "海", "burst_seconds": 8}
        real_replace = os.replace
        seen = []

        def inspect_replace(source, destination):
            source, destination = Path(source), Path(destination)
            seen.append(source)
            self.assertEqual(source.parent.resolve(), self.path.parent.resolve())
            self.assertEqual(destination, self.path)
            self.assertNotEqual(source, destination)
            self.assertEqual(self.path.read_bytes(), old)
            self.assertEqual(pref.read_config(source), cfg)
            # On Windows this also proves the temporary handle is closed.
            return real_replace(source, destination)

        with mock.patch.object(pref.os, "replace", side_effect=inspect_replace) as replace:
            pref.write_config(self.path, cfg)
        replace.assert_called_once()
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())
        self.assertEqual(pref.read_config(self.path), cfg)
        self.assert_only_config_remains()

    def test_relative_destination_is_written_beside_it_not_process_root(self):
        subdirectory = self.directory / "nested"
        subdirectory.mkdir()
        destination = subdirectory / "config.json"
        relative = os.path.relpath(str(destination), os.getcwd())
        pref.write_config(relative, {"ok": True})
        self.assertEqual(pref.read_config(destination), {"ok": True})
        self.assertEqual(list(subdirectory.iterdir()), [destination])
        self.assertEqual(list(self.directory.iterdir()), [subdirectory])

    def test_write_requires_dict_and_leaves_existing_file_untouched(self):
        original = b'{"untouched": true}'
        self.path.write_bytes(original)
        for cfg in ([], (), None, True, "{}", 5):
            with self.subTest(cfg=cfg):
                with self.assertRaisesRegex(pref.ConfigError, "dictionary"):
                    pref.write_config(self.path, cfg)
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_only_config_remains()

    def test_unserializable_or_nonfinite_values_do_not_replace_or_leak_temp(self):
        original = b'{"untouched": true}'
        self.path.write_bytes(original)
        circular = {}
        circular["self"] = circular
        cases = [{"bad": {1, 2}}, {"bad": object()}, {"bad": float("nan")},
                 {"bad": float("inf")}, {"bad": float("-inf")}, circular]
        for cfg in cases:
            with self.subTest(cfg=repr(cfg)):
                with self.assertRaises(pref.ConfigError):
                    pref.write_config(self.path, cfg)
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_only_config_remains()

    def test_temp_creation_failure_does_not_touch_destination(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        error = PermissionError("cannot create temporary file")
        with mock.patch.object(pref.tempfile, "NamedTemporaryFile", side_effect=error):
            with self.assertRaises(pref.ConfigError) as caught:
                pref.write_config(self.path, {"new": True})
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_only_config_remains()

    def test_partial_write_failure_cleans_temp_and_preserves_existing_file(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        real_tempfile = pref.tempfile.NamedTemporaryFile
        created = []

        def failing_tempfile(*args, **kwargs):
            stream = real_tempfile(*args, **kwargs)
            created.append(Path(stream.name))

            def fail_write(text):
                stream.file.write(text[:5])
                raise OSError("disk full during write")

            stream.write = fail_write
            return stream

        with mock.patch.object(pref.tempfile, "NamedTemporaryFile", side_effect=failing_tempfile):
            with self.assertRaisesRegex(pref.ConfigError, "disk full"):
                pref.write_config(self.path, {"new": "partially written"})
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_only_config_remains()

    def test_encoding_failure_cleans_temp_and_preserves_existing_file(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        with self.assertRaises(pref.ConfigError):
            pref.write_config(self.path, {"bad": "\ud800"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_only_config_remains()

    def test_fsync_failure_cleans_temp_and_preserves_existing_file(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        with mock.patch.object(pref.os, "fsync", side_effect=OSError("flush failed")):
            with self.assertRaisesRegex(pref.ConfigError, "flush failed"):
                pref.write_config(self.path, {"new": True})
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_only_config_remains()

    def test_replace_failure_cleans_temp_and_preserves_existing_file(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        error = PermissionError("replace denied")
        with mock.patch.object(pref.os, "replace", side_effect=error) as replace:
            with self.assertRaises(pref.ConfigError) as caught:
                pref.write_config(self.path, {"new": True})
        self.assertIs(caught.exception.__cause__, error)
        temporary_path = Path(replace.call_args[0][0])
        self.assertFalse(temporary_path.exists())
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_only_config_remains()

    def test_replace_failure_for_new_config_leaves_no_file_or_temp(self):
        with mock.patch.object(pref.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(pref.ConfigError):
                pref.write_config(self.path, {"new": True})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_missing_parent_is_not_created_by_writer(self):
        with self.assertRaises(pref.ConfigError):
            pref.write_config(self.directory / "absent" / "config.json", {"new": True})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invalid_write_path_is_reported_without_creating_files(self):
        for path in (None, "", "\x00"):
            with self.subTest(path=path):
                with self.assertRaises(pref.ConfigError):
                    pref.write_config(path, {"new": True})
                self.assertEqual(list(self.directory.iterdir()), [])


class CoordinatedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aerial-update-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "配置 settings.json"
        self.lock_path = Path(str(self.path) + ".lock")

    def assert_config_and_stable_lock_only(self):
        self.assertEqual(set(self.directory.iterdir()), {self.path, self.lock_path})
        self.assertGreaterEqual(self.lock_path.stat().st_size, 1)

    def locking_backend(self):
        if os.name == "nt":
            import msvcrt
            return msvcrt, "locking"
        import fcntl
        return fcntl, "flock"

    def test_missing_configuration_is_created_and_merged_dict_is_returned(self):
        changes = {"burst_seconds": 15, "future": {"海": [1, True]}}
        result = pref.update_config(self.path, changes)
        self.assertEqual(result, changes)
        self.assertIsNot(result, changes)
        self.assertEqual(pref.read_config(self.path), changes)
        self.assert_config_and_stable_lock_only()

    def test_shallow_merge_preserves_unknown_keys_without_mutating_changes(self):
        original = {"burst_seconds": 8, "unknown": {"retain": [None, "海"]},
                    "nested": {"old": True}}
        pref.write_config(self.path, original)
        changes = {"burst_seconds": 15, "nested": {"replacement": True}}
        before = copy.deepcopy(changes)
        result = pref.update_config(self.path, changes)
        expected = dict(original, **changes)
        self.assertEqual(result, expected)
        self.assertEqual(pref.read_config(self.path), expected)
        self.assertEqual(changes, before)
        self.assertEqual(original["nested"], {"old": True})
        self.assert_config_and_stable_lock_only()

    def test_each_update_reads_fresh_instead_of_reusing_stale_configuration(self):
        pref.write_config(self.path, {"burst_seconds": 8, "unknown": "keep"})
        pref.update_config(self.path, {"burst_seconds": 15})
        result = pref.update_config(self.path, {"original_wallpaper": "C:/海.jpg"})
        self.assertEqual(result, {"burst_seconds": 15, "unknown": "keep",
                                  "original_wallpaper": "C:/海.jpg"})
        self.assertEqual(pref.read_config(self.path), result)

    def test_missing_only_replaces_every_falsy_value_but_no_truthy_value(self):
        original = {"none": None, "false": False, "zero": 0, "empty_string": "",
                    "empty_list": [], "empty_dict": {}, "wallpaper": "original.jpg",
                    "true": True, "number": 2, "list": [0], "dict": {"v": 0}}
        pref.write_config(self.path, original)
        changes = {key: "replacement" for key in original}
        changes["absent"] = "new value"
        expected = {key: (value if value else "replacement")
                    for key, value in original.items()}
        expected["absent"] = "new value"
        result = pref.update_config(self.path, changes, missing_only=True)
        self.assertEqual(result, expected)
        self.assertEqual(pref.read_config(self.path), expected)
        self.assert_config_and_stable_lock_only()

    def test_missing_only_remembers_original_once_and_keeps_later_panel_changes(self):
        pref.update_config(self.path, {"original_wallpaper": "first.jpg"}, missing_only=True)
        pref.update_config(self.path, {"burst_seconds": 15})
        result = pref.update_config(self.path, {"original_wallpaper": "second.jpg"},
                                    missing_only=True)
        self.assertEqual(result, {"original_wallpaper": "first.jpg", "burst_seconds": 15})

    def test_non_dictionary_changes_fail_before_creating_sidecar(self):
        for changes in (None, [], (), True, "{}", 8):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(pref.ConfigError, "changes.*dictionary"):
                    pref.update_config(self.path, changes)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_corrupt_invalid_utf8_and_nonobject_input_never_get_overwritten(self):
        for raw in (b'{"video":', b"[]", b"null", b'{"bad": NaN}', b'{"bad": "\xff"}'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(pref.ConfigError):
                    pref.update_config(self.path, {"burst_seconds": 15})
                self.assertEqual(self.path.read_bytes(), raw)
                self.assert_config_and_stable_lock_only()
        # A subsequent call succeeds: every failed read released its lock.
        pref.write_config(self.path, {"repaired_explicitly": True})
        self.assertEqual(pref.update_config(self.path, {"burst_seconds": 15}),
                         {"repaired_explicitly": True, "burst_seconds": 15})

    def test_read_failure_preserves_data_and_releases_lock(self):
        original = {"unknown": "keep"}
        pref.write_config(self.path, original)
        with mock.patch.object(pref, "read_config", side_effect=pref.ConfigError("read denied")):
            with self.assertRaisesRegex(pref.ConfigError, "read denied"):
                pref.update_config(self.path, {"burst_seconds": 15})
        self.assertEqual(pref.read_config(self.path), original)
        self.assertEqual(pref.update_config(self.path, {"burst_seconds": 15}),
                         dict(original, burst_seconds=15))
        self.assert_config_and_stable_lock_only()

    def test_atomic_replace_failure_preserves_data_cleans_temp_and_releases_lock(self):
        original = b'{"unknown": "keep"}'
        self.path.write_bytes(original)
        with mock.patch.object(pref.os, "replace", side_effect=PermissionError("replace denied")):
            with self.assertRaisesRegex(pref.ConfigError, "replace denied"):
                pref.update_config(self.path, {"burst_seconds": 15})
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_config_and_stable_lock_only()
        self.assertEqual(pref.update_config(self.path, {"burst_seconds": 15}),
                         {"unknown": "keep", "burst_seconds": 15})

    def test_serialization_errors_preserve_data_clean_temp_and_release_lock(self):
        original = b'{"unknown": "keep"}'
        for bad in ({1, 2}, object(), float("nan"), float("inf"), "\ud800"):
            with self.subTest(bad=repr(bad)):
                self.path.write_bytes(original)
                changes = {"bad": bad}
                with self.assertRaises(pref.ConfigError):
                    pref.update_config(self.path, changes)
                self.assertIs(changes["bad"], bad)
                self.assertEqual(self.path.read_bytes(), original)
                self.assert_config_and_stable_lock_only()
                self.assertEqual(pref.update_config(self.path, {"recovered": True}),
                                 {"unknown": "keep", "recovered": True})

    def test_sidecar_is_never_truncated_replaced_or_unlinked(self):
        marker = b"stable existing lock file\n"
        self.lock_path.write_bytes(marker)
        initial = self.lock_path.stat()
        for value in (8, 15, 30):
            pref.update_config(self.path, {"burst_seconds": value})
            current = self.lock_path.stat()
            self.assertEqual((current.st_dev, current.st_ino), (initial.st_dev, initial.st_ino))
            self.assertEqual(self.lock_path.read_bytes(), marker)
            self.assert_config_and_stable_lock_only()

    def test_lock_open_permission_failure_is_config_error_without_touching_data(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        error = PermissionError("sidecar denied")
        with mock.patch("builtins.open", side_effect=error):
            with self.assertRaisesRegex(pref.ConfigError, "sidecar denied") as caught:
                pref.update_config(self.path, {"burst_seconds": 15})
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_invalid_paths_and_missing_parent_are_config_errors(self):
        for path in (None, "", "\x00", self.directory / "absent" / "config.json"):
            with self.subTest(path=path):
                with self.assertRaises(pref.ConfigError):
                    pref.update_config(path, {"burst_seconds": 15})
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_real_lock_contention_times_out_promptly_without_read_or_overwrite(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        with pref._config_lock(str(self.lock_path)):
            with mock.patch.object(pref, "_CONFIG_LOCK_TIMEOUT", 0.04):
                with mock.patch.object(pref, "read_config") as read:
                    started = time.monotonic()
                    with self.assertRaisesRegex(pref.ConfigError, "Timed out.*configuration lock"):
                        pref.update_config(self.path, {"burst_seconds": 15})
                    elapsed = time.monotonic() - started
                read.assert_not_called()
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_config_and_stable_lock_only()
        self.assertEqual(pref.update_config(self.path, {"burst_seconds": 15}),
                         {"keep": True, "burst_seconds": 15})

    def test_noncontention_lock_error_is_reported_without_retry(self):
        original = b'{"keep": true}'
        self.path.write_bytes(original)
        backend, function = self.locking_backend()
        error = OSError(errno.EBADF, "invalid lock handle")
        with mock.patch.object(backend, function, side_effect=error):
            with mock.patch.object(pref.time, "sleep") as sleep:
                with self.assertRaisesRegex(pref.ConfigError, "invalid lock handle") as caught:
                    pref.update_config(self.path, {"burst_seconds": 15})
        sleep.assert_not_called()
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(set(self.directory.iterdir()), {self.path, self.lock_path})
        self.assertEqual(self.lock_path.stat().st_size, 0)
        # Initializing the lock byte is itself protected by a successful lock.
        pref.update_config(self.path, {"burst_seconds": 15})
        self.assert_config_and_stable_lock_only()

    def test_platform_lock_modules_are_not_imported_until_an_update(self):
        import builtins
        real_import = builtins.__import__

        def no_lock_import(name, *args, **kwargs):
            if name in ("msvcrt", "fcntl"):
                raise AssertionError("Lock module imported eagerly: " + name)
            return real_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("pure_preferences_import_test", pref.__file__)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("builtins.__import__", side_effect=no_lock_import):
            spec.loader.exec_module(module)
        self.assertEqual(module.burst_seconds("15.9"), 15)
        self.assertTrue(callable(module.update_config))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_two_process_updates_are_serialized_and_read_fresh_under_lock(self):
        # Both children import only this pure helper module. No application,
        # GUI, player, pipes, sockets, or real configuration files are involved.
        original = {"burst_seconds": 8, "unknown": {"keep": "海"}}
        pref.write_config(self.path, original)
        first_read = self.directory / "first-read"
        second_read = self.directory / "second-read"
        blocked = self.directory / "second-blocked"
        release = self.directory / "release-first"
        code = r'''
import sys
import time
from pathlib import Path
import preferences as pref

path, first_read, second_read, blocked, release = map(Path, sys.argv[2:])
real_read = pref.read_config
if sys.argv[1] == "first":
    def paused_read(config_path):
        cfg = real_read(config_path)
        first_read.write_bytes(b"read under lock")
        deadline = time.monotonic() + 5.0
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Test release marker was not delivered")
            time.sleep(0.01)
        return cfg
    pref.read_config = paused_read
    pref.update_config(path, {"original_wallpaper": "remembered.jpg"}, missing_only=True)
else:
    real_sleep = time.sleep
    def observe_contention(delay):
        blocked.write_bytes(b"OS lock rejected overlapping acquisition")
        real_sleep(delay)
    def observed_read(config_path):
        cfg = real_read(config_path)
        second_read.write_bytes(b"read after lock")
        return cfg
    pref.time.sleep = observe_contention
    pref.read_config = observed_read
    pref.update_config(path, {"burst_seconds": 15})
'''

        def stop_process(process):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5.0)

        def launch(mode):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", code, mode, str(self.path), str(first_read),
                 str(second_read), str(blocked), str(release)],
                cwd=os.path.dirname(os.path.abspath(pref.__file__)),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            )
            self.addCleanup(stop_process, process)
            return process

        def wait_for_marker(marker, process):
            deadline = time.monotonic() + 5.0
            while not marker.exists():
                if process.poll() is not None:
                    self.fail("Pure helper worker exited before {} (exit {})".format(
                        marker.name, process.returncode))
                if time.monotonic() >= deadline:
                    self.fail("Pure helper worker did not reach " + marker.name)
                time.sleep(0.01)

        first = launch("first")
        try:
            wait_for_marker(first_read, first)
            second = launch("second")
            wait_for_marker(blocked, second)
            self.assertFalse(second_read.exists(), "Second writer read before obtaining the lock")
            self.assertEqual(pref.read_config(self.path), original)
            release.write_bytes(b"continue")
            self.assertEqual(first.wait(timeout=5.0), 0)
            self.assertEqual(second.wait(timeout=5.0), 0)
        finally:
            release.write_bytes(b"continue")
        self.assertTrue(second_read.exists())
        self.assertEqual(pref.read_config(self.path), dict(
            original, burst_seconds=15, original_wallpaper="remembered.jpg"))
        self.assertTrue(self.lock_path.exists())


class BurstTests(unittest.TestCase):
    def test_legacy_numbers_truncate_then_clamp(self):
        cases = [(3, 3), (8, 8), (30, 30), (0, 3), (-17, 3), (31, 30),
                 (1000, 30), (3.99, 3), (8.99, 8), (-4.9, 3), (30.99, 30),
                 (Decimal("15.9"), 15), (Fraction(17, 2), 8)]
        for value, expected in cases:
            with self.subTest(value=value):
                result = pref.burst_seconds(value)
                self.assertEqual(result, expected)
                self.assertIs(type(result), int)

    def test_legacy_numeric_strings(self):
        cases = [("3", 3), (" 15 ", 15), ("008", 8), ("8.9", 8),
                 ("3e1", 30), ("2.99", 3), (".8", 3), ("-12.5", 3),
                 ("+9", 9), ("30.99", 30)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(pref.burst_seconds(value), expected)

    def test_large_finite_values_clamp_without_float_or_integer_overflow(self):
        cases = [(10 ** 400, 30), (-(10 ** 400), 3), ("1e999999", 30),
                 ("-1e999999", 3), (Decimal("1e999999"), 30),
                 (Fraction(10 ** 400, 3), 30)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(pref.burst_seconds(value), expected)

    def test_bad_legacy_values_use_default_not_clamp(self):
        cases = [None, True, False, "", "   ", "eight", "8 seconds", "0x10",
                 [], {}, object(), complex(8, 0), float("nan"), float("inf"),
                 float("-inf"), "NaN", "Infinity", "-Infinity", "sNaN",
                 Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")]
        for value in cases:
            with self.subTest(value=value):
                result = pref.burst_seconds(value)
                self.assertEqual(result, pref.DEFAULT_BURST)
                self.assertIs(type(result), int)

    def test_strict_ui_validation_accepts_only_integral_decimal_text_in_range(self):
        cases = [("3", 3), ("8", 8), ("15", 15), ("30", 30),
                 (" 8 ", 8), ("\t15\n", 15), ("003", 3), ("00030", 30),
                 ("0" * 5000 + "8", 8)]
        for text, expected in cases:
            with self.subTest(text=text[:40]):
                result = pref.validate_burst(text)
                self.assertEqual(result, expected)
                self.assertIs(type(result), int)

    def test_ui_validation_rejects_without_rounding_or_clamping(self):
        cases = ["2", "31", "0", "-5", "+8", "8.0", "8.9", "1e1", "0x8",
                 "8_0", "08.0", " ", "", "eight", "８", "٣", "8 0", "8\n9",
                 "\x008", "9" * 5000, None, True, 8, 8.0, []]
        for text in cases:
            with self.subTest(text=repr(text)[:60]):
                with self.assertRaisesRegex(ValueError, "3.*30.*整数"):
                    pref.validate_burst(text)

    def test_every_preset_is_valid_ui_input(self):
        for preset in pref.BURST_PRESETS:
            with self.subTest(preset=preset):
                self.assertEqual(pref.validate_burst(str(preset)), preset)


class PathAndNameTests(unittest.TestCase):
    def test_same_path_normalizes_absolute_relative_and_dot_segments(self):
        target = Path(os.getcwd()) / "not-created" / "城市_夜景.mov"
        equivalent = target.parent / "subdir" / ".." / target.name
        self.assertTrue(pref.same_path(target, equivalent))
        self.assertTrue(pref.same_path(target, os.path.relpath(str(target))))
        self.assertTrue(pref.same_path(target, os.fsencode(str(target))))
        self.assertIs(type(pref.same_path(target, equivalent)), bool)

    def test_different_paths_are_not_equal(self):
        self.assertFalse(pref.same_path("video_a.mov", "video_b.mov"))
        self.assertFalse(pref.same_path("folder_a/video.mov", "folder_b/video.mov"))

    def test_empty_blank_and_invalid_paths_never_compare_equal(self):
        for value in (None, "", b"", "   ", "\t\n", "\x00", 0, False, object()):
            with self.subTest(value=value):
                self.assertFalse(pref.same_path(value, value))
                self.assertFalse(pref.same_path("video.mov", value))
                self.assertFalse(pref.same_path(value, "video.mov"))

    def test_path_case_follows_native_platform(self):
        self.assertEqual(pref.same_path("VIDEO.MOV", "video.mov"), os.name == "nt")

    @unittest.skipUnless(os.name == "nt", "Windows-only native path normalization")
    def test_windows_paths_ignore_case_slashes_and_parent_segments(self):
        self.assertTrue(pref.same_path(r"C:\Media\城市\..\VIDEO.MOV", "c:/media/video.mov"))
        self.assertTrue(pref.same_path(r"\\server\Share\Video.mov", "//SERVER/share/video.MOV"))
        self.assertFalse(pref.same_path(r"C:\Media\Video.mov", r"D:\Media\Video.mov"))

    def test_friendly_name_cleanup_is_portable_and_unicode_safe(self):
        cases = [
            (r"C:\Media\Africa_Night_1_4K.MOV", "Africa Night 1 4K"),
            ("/library/城市_夜景.mp4", "城市 夜景"),
            ("/library.with.dots/scene.v2_4K.mp4", "scene.v2 4K"),
            ("plain_file", "plain file"),
            (".hidden_video", ".hidden video"),
            (".hidden_video.mp4", ".hidden video"),
            ("trailing.", "trailing"),
            ("____.mp4", "    "),
            ("", ""),
            ("/folder/", ""),
            (Path("a_folder") / "Moon_Light.mkv", "Moon Light"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(pref.friendly_name(path), expected)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            ("Alpine Dawn", "/media/alps_morning_4K.mov", "thumbs/first.jpg"),
            ("城市 夜景", r"C:\影片\Shanghai_Night_4K.mp4", "thumbs/second.jpg"),
            ("Straße River", "/media/RIVER_day_4k.MOV", ""),
            ("Ocean Evening", "/media/Sea_Night_1080p.mp4", "needle/preview.jpg"),
            ("Alpine Dawn", "/elsewhere/other_4K.mp4", ""),
            ("上海 夜景", "/media/sunset.MOV", ""),
        ]

    def test_empty_queries_return_all_original_indexes_in_order(self):
        for query in ("", " ", "\t\n", "\u3000"):
            with self.subTest(query=query):
                self.assertEqual(pref.filter_library(self.items, query), list(range(6)))

    def test_name_or_basename_matching_is_casefolded(self):
        cases = [("alpine", [0, 4]), ("SHANGHAI", [1]), (".mov", [0, 2, 5]),
                 ("STRASSE", [2]), ("城市 夜景", [1]), ("上海", [5])]
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(pref.filter_library(self.items, query), expected)

    def test_all_tokens_must_match_but_can_span_name_and_basename(self):
        cases = [("ocean night", [3]), ("night 4K", [1]), ("alpine 4k", [0, 4]),
                 ("  RIVER\t4K\n", [2]), ("night absent", []), ("night night", [1, 3])]
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(pref.filter_library(self.items, query), expected)

    def test_directories_and_thumbnails_are_not_searchable(self):
        self.assertEqual(pref.filter_library(self.items, "media"), [])
        self.assertEqual(pref.filter_library(self.items, "needle"), [])
        self.assertEqual(pref.filter_library(self.items, "preview"), [])

    def test_filtered_selection_maps_to_original_item_not_display_row(self):
        indexes = pref.filter_library(self.items, "night")
        self.assertIs(type(indexes), list)
        self.assertEqual(indexes, [1, 3])
        displayed_row = 1
        original_index = indexes[displayed_row]
        self.assertEqual(original_index, 3)
        self.assertIs(self.items[original_index], self.items[3])
        self.assertNotEqual(self.items[original_index], self.items[displayed_row])
        self.assertEqual(pref.filter_library(self.items, "1080p"), [original_index])

    def test_filter_does_not_modify_or_reorder_items(self):
        original = list(self.items)
        identities = [id(item) for item in self.items]
        pref.filter_library(self.items, "4k")
        self.assertEqual(self.items, original)
        self.assertEqual([id(item) for item in self.items], identities)

    def test_empty_library_and_iterable_input(self):
        self.assertEqual(pref.filter_library([], ""), [])
        self.assertEqual(pref.filter_library([], "night"), [])
        self.assertEqual(pref.filter_library(iter(self.items), "night"), [1, 3])


class LayoutTests(unittest.TestCase):
    def assert_fits(self, width, height, dpi):
        scale = pref.fit_board(width, height, dpi)
        self.assertIs(type(scale), float)
        self.assertTrue(math.isfinite(scale))
        self.assertGreater(scale, 0.0)
        self.assertLessEqual(scale, dpi)
        self.assertLessEqual(pref.BOARD_WIDTH * scale + 32.0, width + 1e-9)
        self.assertLessEqual(pref.BOARD_HEIGHT * scale + 32.0, height + 1e-9)
        expected = min(dpi, (width - 32.0) / pref.BOARD_WIDTH,
                       (height - 32.0) / pref.BOARD_HEIGHT)
        self.assertAlmostEqual(scale, expected)

    def test_common_work_areas_fit_at_100_through_200_percent_dpi(self):
        sizes = [(1366, 768), (1366, 728), (1920, 1080), (1920, 1040),
                 (2560, 1440), (3840, 2160), (1280, 720), (1024, 768),
                 (800, 600), (720, 1280)]
        for width, height in sizes:
            for dpi in (1.0, 1.25, 1.5, 1.75, 2.0):
                with self.subTest(width=width, height=height, dpi=dpi):
                    self.assert_fits(width, height, dpi)

    def test_1366_by_768_never_overflows_at_high_dpi(self):
        self.assertEqual(pref.fit_board(1366, 768, 1.0), 1.0)
        for dpi in (1.25, 1.5, 1.75, 2.0):
            with self.subTest(dpi=dpi):
                self.assertAlmostEqual(pref.fit_board(1366, 768, dpi), 736.0 / 720.0)

    def test_exact_fit_reserves_sixteen_physical_pixels_per_edge(self):
        self.assertEqual(pref.fit_board(1096, 752), 1.0)
        self.assertEqual(pref.fit_board(2160, 1472, 2.0), 2.0)
        self.assertEqual(pref.fit_board(1096, 752, 2.0), 1.0)

    def test_large_area_honors_requested_dpi_instead_of_upscaling(self):
        for dpi in (0.01, 0.25, 1.0, 1.5, 2.0):
            with self.subTest(dpi=dpi):
                self.assertEqual(pref.fit_board(10000, 10000, dpi), dpi)

    def test_tiny_areas_do_not_force_overflowing_minimum_scale(self):
        for width, height in ((100, 100), (300, 200), (33, 33), (400, 80)):
            with self.subTest(width=width, height=height):
                self.assert_fits(width, height, 2.0)
        self.assertLess(pref.fit_board(100, 100, 2.0), 0.1)

    def test_invalid_or_margin_consumed_dimensions_return_zero(self):
        bad = [None, True, False, "invalid", "", float("nan"), float("inf"),
               float("-inf"), -500, 0, 1, 16, 32, 10 ** 400]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(pref.fit_board(value, 768, 1.5), 0.0)
                self.assertEqual(pref.fit_board(1366, value, 1.5), 0.0)

    def test_invalid_or_nonpositive_dpi_predictably_defaults_to_one(self):
        bad = [None, True, False, "invalid", "", float("nan"), float("inf"),
               float("-inf"), -1, 0, 10 ** 400]
        for dpi in bad:
            with self.subTest(dpi=dpi):
                self.assertEqual(pref.fit_board(4000, 3000, dpi), 1.0)
                self.assertEqual(pref.fit_board(800, 600, dpi), pref.fit_board(800, 600, 1.0))

    def test_numeric_strings_and_fractional_dimensions(self):
        self.assertEqual(pref.fit_board("1366", "768", "1.5"), pref.fit_board(1366, 768, 1.5))
        self.assert_fits(1366.5, 768.25, 1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
