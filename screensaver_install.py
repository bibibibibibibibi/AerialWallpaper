"""Per-user screen-saver installation; importing this module has no side effects."""
import os
import shutil
import subprocess
import sys
import tempfile
from string import Template

SCR_NAME = "AerialScreensaver.scr"
_DESKTOP_KEY = r"Control Panel\Desktop"
_MISSING = object()


def _csharp_string(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


def _launcher_source(app_dir, python):
    # Quote follows the Windows C-runtime argv rules, not shell escaping.
    return Template(r'''using System;
using System.Diagnostics;
using System.Text;
internal static class SaverLauncher {
    private static string Quote(string value) {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value) {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') {
                result.Append('\\', slashes * 2 + 1);
                result.Append(ch);
            } else {
                result.Append('\\', slashes);
                result.Append(ch);
            }
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }
    [STAThread]
    private static int Main(string[] args) {
        try {
            if (args.Length == 0) args = new string[] { "/s" };
            StringBuilder arguments = new StringBuilder("-B ");
            arguments.Append(Quote($script));
            foreach (string arg in args) {
                arguments.Append(' ');
                arguments.Append(Quote(arg));
            }
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = $python;
            start.Arguments = arguments.ToString();
            start.WorkingDirectory = $directory;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process child = Process.Start(start)) {
                if (child == null) return 1;
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch { return 1; }
    }
}
''').substitute(script=_csharp_string(os.path.join(app_dir, 'screensaver.py')),
               python=_csharp_string(python), directory=_csharp_string(app_dir))


def _find_compiler():
    windows = os.environ.get("WINDIR", r"C:\Windows")
    for architecture in ("Framework64", "Framework"):
        candidate = os.path.join(windows, "Microsoft.NET", architecture,
                                 "v4.0.30319", "csc.exe")
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError("找不到 Windows .NET Framework C# 编译器 csc.exe，无法创建源码版屏保。")


def _install_artifact(app_dir, destination, executable):
    if executable is not None:
        executable = os.path.abspath(executable)
        if not os.path.isfile(executable):
            raise RuntimeError("找不到当前程序 exe：" + executable)
        if os.path.normcase(executable) == os.path.normcase(destination):
            return
    else:
        if not os.path.isfile(os.path.join(app_dir, "screensaver.py")):
            raise RuntimeError("找不到源码屏保 screensaver.py：" + app_dir)
        compiler = _find_compiler()
        python = os.path.abspath(sys.executable)
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(pythonw):
            python = pythonw
        if not os.path.isfile(python):
            raise RuntimeError("找不到 Python 解释器：" + python)
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".saver-build-", dir=parent) as staging:
        artifact = os.path.join(staging, SCR_NAME)
        if executable is not None:
            shutil.copyfile(executable, artifact)
        else:
            source = os.path.join(staging, "Launcher.cs")
            with open(source, "w", encoding="utf-8-sig") as stream:
                stream.write(_launcher_source(app_dir, python))
            command = [compiler, "/nologo", "/target:winexe", "/out:" + artifact]
            icon = os.path.join(app_dir, "assets", "icon.ico")
            if os.path.isfile(icon):
                command.append("/win32icon:" + icon)
            command.append(source)
            result = subprocess.run(command, capture_output=True, text=True,
                                    errors="replace", timeout=60,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not os.path.isfile(artifact):
                detail = (result.stderr or result.stdout or "未生成屏保文件").strip()
                raise RuntimeError("创建屏保启动器失败：" + detail[:2000])
        os.replace(artifact, destination)


def _registry_module():
    import winreg
    return winreg


def _notify_active(active):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    spi = user32.SystemParametersInfoW
    spi.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
    spi.restype = wintypes.BOOL
    # Registry writes are explicit below; SENDCHANGE updates Windows' cached state.
    if not spi(0x0011, int(bool(active)), None, 0x0002):
        raise ctypes.WinError(ctypes.get_last_error())


def _register(destination):
    registry = _registry_module()
    access = registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE
    with registry.OpenKey(registry.HKEY_CURRENT_USER, _DESKTOP_KEY, 0, access) as key:
        names = ("SCRNSAVE.EXE", "ScreenSaveActive", "ScreenSaveTimeOut")
        previous = {}
        for name in names:
            try:
                previous[name] = registry.QueryValueEx(key, name)
            except FileNotFoundError:
                previous[name] = _MISSING
        touched = []
        try:
            updates = [("SCRNSAVE.EXE", destination), ("ScreenSaveActive", "1")]
            if previous["ScreenSaveTimeOut"] is _MISSING:
                updates.append(("ScreenSaveTimeOut", "600"))
            for name, value in updates:
                touched.append(name)
                registry.SetValueEx(key, name, 0, registry.REG_SZ, value)
            _notify_active(True)
            for name, expected in updates:
                actual, kind = registry.QueryValueEx(key, name)
                if actual != expected or kind != registry.REG_SZ:
                    raise RuntimeError("屏保注册表写入校验失败：" + name)
        except Exception as error:
            rollback_errors = []
            for name in reversed(touched):
                try:
                    old = previous[name]
                    if old is _MISSING:
                        try:
                            registry.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
                    else:
                        value, kind = old
                        registry.SetValueEx(key, name, 0, kind, value)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            old_active = previous["ScreenSaveActive"]
            try:
                _notify_active(old_active is not _MISSING and str(old_active[0]) == "1")
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise RuntimeError(str(error) + "；恢复原设置失败：" + "; ".join(rollback_errors)) from error
            raise


def install_screensaver(app_dir, executable=None):
    """Build/copy a real .scr before registering it; return (success, detail)."""
    try:
        app_dir = os.path.abspath(os.fspath(app_dir))
        if executable is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise RuntimeError("找不到 LOCALAPPDATA，无法确定当前用户屏保安装目录。")
            destination = os.path.join(os.path.abspath(local), "AerialWallpaper", "screensaver", SCR_NAME)
        else:
            destination = os.path.join(app_dir, SCR_NAME)
        _install_artifact(app_dir, destination, executable)
        _register(destination)
        return True, destination
    except Exception as error:
        return False, "安装屏保失败：" + str(error)
