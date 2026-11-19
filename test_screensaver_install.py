"""Safe installer tests: temporary files, fake registry, no native calls/compiler."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import screensaver_install as installer


class FakeRegistry:
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    HKEY_CURRENT_USER = object()
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []
        self.fail_name = None
        self.corrupt_readback = False

    def OpenKey(self, *args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        if self.corrupt_readback and self.writes and name == "SCRNSAVE.EXE":
            return "wrong path", self.REG_SZ
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        if self.fail_name == name:
            self.fail_name = None
            raise OSError("injected write failure")
        self.writes.append((name, value))
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        self.values.pop(name, None)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "源码 with spaces"
        self.app.mkdir()
        (self.app / "screensaver.py").write_text("# inert test source\n", encoding="utf-8")
        self.python = self.root / "Python with spaces" / "python.exe"
        self.python.parent.mkdir()
        self.python.write_bytes(b"inert interpreter placeholder")
        self.pythonw = self.python.with_name("pythonw.exe")
        self.pythonw.write_bytes(b"inert windowless placeholder")
        self.registry = FakeRegistry({"SCRNSAVE.EXE": ("old.scr", 1),
                                      "ScreenSaveActive": ("0", 1),
                                      "ScreenSaveTimeOut": ("1234", 1),
                                      "ScreenSaverIsSecure": ("1", 1)})
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")}).start()
        patch.object(installer.sys, "executable", str(self.python)).start()
        patch.object(installer, "_registry_module", return_value=self.registry).start()
        self.notify = patch.object(installer, "_notify_active").start()
        self.compiler = patch.object(installer, "_find_compiler", return_value="fake-csc.exe").start()
        self.run = patch.object(installer.subprocess, "run", side_effect=self.compile_stub).start()
        self.compiled_source = None

    def compile_stub(self, command, **kwargs):
        self.compiled_source = Path(command[-1]).read_text(encoding="utf-8-sig")
        output = next(arg[5:] for arg in command if arg.startswith("/out:"))
        Path(output).write_bytes(b"MZ inert compiled placeholder")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_source_install_preserves_timeout_secure_and_registers_last(self):
        old_values = dict(self.registry.values)
        def check_artifact(active):
            destination = self.registry.values["SCRNSAVE.EXE"][0]
            self.assertTrue(Path(destination).is_file())
        self.notify.side_effect = check_artifact
        ok, destination = installer.install_screensaver(self.app)
        self.assertTrue(ok, destination)
        self.assertEqual(Path(destination), self.root / "local" / "AerialWallpaper" / "screensaver" / installer.SCR_NAME)
        self.assertEqual(old_values["ScreenSaveTimeOut"], self.registry.values["ScreenSaveTimeOut"])
        self.assertEqual(old_values["ScreenSaverIsSecure"], self.registry.values["ScreenSaverIsSecure"])
        self.assertEqual([("SCRNSAVE.EXE", destination), ("ScreenSaveActive", "1")], self.registry.writes)
        self.notify.assert_called_once_with(True)
        self.assertIn(installer._csharp_string(str(self.pythonw)), self.compiled_source)
        self.assertIn(installer._csharp_string(str(self.app / "screensaver.py")), self.compiled_source)

    def test_missing_timeout_gets_default(self):
        del self.registry.values["ScreenSaveTimeOut"]
        self.assertTrue(installer.install_screensaver(self.app)[0])
        self.assertEqual(("600", 1), self.registry.values["ScreenSaveTimeOut"])

    def test_frozen_copies_only_supplied_executable(self):
        executable = self.app / "preserved.exe"
        executable.write_bytes(b"MZ preserved")
        ok, destination = installer.install_screensaver(self.app, str(executable))
        self.assertTrue(ok, destination)
        self.assertEqual(executable.read_bytes(), Path(destination).read_bytes())
        self.assertEqual(b"MZ preserved", executable.read_bytes())
        self.run.assert_not_called()
        self.compiler.assert_not_called()

    def test_frozen_same_destination_does_not_copy(self):
        executable = self.app / installer.SCR_NAME
        executable.write_bytes(b"MZ same file")
        with patch.object(installer.shutil, "copyfile") as copy:
            self.assertTrue(installer.install_screensaver(self.app, str(executable))[0])
            copy.assert_not_called()

    def test_compiler_failure_leaves_registry_and_previous_artifact_untouched(self):
        destination = self.root / "local" / "AerialWallpaper" / "screensaver" / installer.SCR_NAME
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old artifact")
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 1, "", "bad compiler")
        previous = dict(self.registry.values)
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("bad compiler", error)
        self.assertEqual(b"old artifact", destination.read_bytes())
        self.assertEqual(previous, self.registry.values)
        self.notify.assert_not_called()

    def test_compiler_missing_reports_clear_failure(self):
        self.compiler.side_effect = RuntimeError("找不到 csc.exe")
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("csc.exe", error)
        self.assertEqual([], self.registry.writes)
        self.run.assert_not_called()

    def test_source_missing_does_not_compile_or_register(self):
        (self.app / "screensaver.py").unlink()
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("screensaver.py", error)
        self.assertEqual([], self.registry.writes)
        self.run.assert_not_called()

    def test_notification_failure_rolls_back_all_values(self):
        previous = dict(self.registry.values)
        self.notify.side_effect = [OSError("SPI failed"), None]
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("SPI failed", error)
        self.assertEqual(previous, self.registry.values)
        self.assertEqual([((True,), {}), ((False,), {})], self.notify.call_args_list)

    def test_partial_write_failure_restores_previous_values(self):
        previous = dict(self.registry.values)
        self.registry.fail_name = "ScreenSaveActive"
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("injected", error)
        self.assertEqual(previous, self.registry.values)

    def test_rollback_deletes_originally_missing_values(self):
        self.registry.values.clear()
        self.notify.side_effect = [OSError("SPI failed"), None]
        self.assertFalse(installer.install_screensaver(self.app)[0])
        self.assertEqual({}, self.registry.values)

    def test_readback_failure_is_reported_and_rolled_back(self):
        previous = dict(self.registry.values)
        self.registry.corrupt_readback = True
        ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("校验失败", error)
        self.assertEqual(previous, self.registry.values)

    def test_launcher_lifetime_default_and_windows_quoting(self):
        source = installer._launcher_source('C:\\DIRECTORY $path\\a"b', 'C:\\PYTHON\\pythonw.exe')
        self.assertIn('new string[] { "/s" }', source)
        self.assertIn('new StringBuilder("-B ")', source)
        self.assertIn('foreach (string arg in args)', source)
        self.assertIn('child.WaitForExit();', source)
        self.assertIn('return child.ExitCode;', source)
        self.assertIn('start.UseShellExecute = false;', source)
        self.assertIn('start.CreateNoWindow = true;', source)
        self.assertIn("slashes * 2 + 1", source)
        self.assertIn("slashes * 2", source)
        self.assertIn('C:\\\\DIRECTORY $path\\\\a\\"b', source)
        self.assertEqual('"C:\\\\space path\\\\a\\"b\\\\"', installer._csharp_string('C:\\space path\\a"b\\'))

    def test_no_localappdata_does_not_touch_registry(self):
        with patch.dict(os.environ, {}, clear=True):
            ok, error = installer.install_screensaver(self.app)
        self.assertFalse(ok)
        self.assertIn("LOCALAPPDATA", error)
        self.assertEqual([], self.registry.writes)

    def test_source_uses_python_exe_if_pythonw_absent(self):
        self.pythonw.unlink()
        self.assertTrue(installer.install_screensaver(self.app)[0])
        self.assertIn(installer._csharp_string(str(self.python)), self.compiled_source)


if __name__ == "__main__":
    unittest.main()
