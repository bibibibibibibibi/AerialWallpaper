"""Safe screen-saver entrypoint/launch tests: no playback or setting changes."""
import ctypes
import sys
import unittest
from unittest.mock import patch

import app
import screensaver
import screensaver_install


class ScreensaverRuntimeTests(unittest.TestCase):
    def test_source_install_delegates_without_an_absent_packaged_exe(self):
        with patch.object(screensaver, 'FROZEN', False), patch.object(
                screensaver_install, 'install_screensaver', return_value=(True, 'test.scr')) as install:
            self.assertEqual(screensaver.install_screensaver(), (True, 'test.scr'))
        install.assert_called_once_with(screensaver.APP_DIR, None)

    def test_frozen_install_uses_running_executable(self):
        with patch.object(screensaver, 'FROZEN', True), patch.object(
                screensaver_install, 'install_screensaver', return_value=(True, 'test.scr')) as install:
            screensaver.install_screensaver()
        install.assert_called_once_with(screensaver.APP_DIR, sys.executable)

    def test_saver_renderer_is_isolated_and_embedding_capable(self):
        with patch.object(screensaver, 'find_mpv', return_value='engine.exe'), patch.object(
                screensaver.subprocess, 'Popen') as launch:
            screensaver.launch_mpv(123, 'movie name.mov', {})
        argv = launch.call_args.args[0]
        for flag in ('--no-config', '--load-scripts=no', '--wid=123', '--vo=gpu-next,gpu,direct3d', '--no-stop-screensaver'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-2:], ['--', 'movie name.mov'])
        self.assertFalse(any('input-ipc-server=' in flag for flag in argv))
        self.assertEqual(launch.call_args.kwargs['stdin'], screensaver.subprocess.DEVNULL)

    def test_preview_accepts_separated_and_colon_hwnd(self):
        for args in (['/p', '123'], ['/P:123'], ['-p:123']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['saver.scr'] + args), patch.object(
                    screensaver, 'run_preview') as preview, patch.object(screensaver, 'run_fullscreen') as full:
                screensaver.main()
                preview.assert_called_once_with(123)
                full.assert_not_called()

    def test_invalid_preview_does_not_launch(self):
        for args in (['/p'], ['/p:not-a-handle']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['saver.scr'] + args), patch.object(
                    screensaver, 'run_preview') as preview, patch.object(screensaver, 'run_fullscreen') as full:
                screensaver.main()
                preview.assert_not_called()
                full.assert_not_called()

    def test_colon_config_is_dispatched_without_playback(self):
        with patch.object(sys, 'argv', ['saver.scr', '/C:123']), patch.object(
                screensaver, 'show_config') as config, patch.object(screensaver, 'run_fullscreen') as full:
            screensaver.main()
        config.assert_called_once_with()
        full.assert_not_called()

    def test_packaged_app_dispatches_colon_saver_flags(self):
        for arg in ('/P:123', '/c:123'):
            with self.subTest(arg=arg), patch.object(sys, 'argv', ['app.exe', arg]), patch.object(
                    screensaver, 'main') as saver, patch.object(app, 'run_ui') as ui:
                app.main()
                saver.assert_called_once_with()
                ui.assert_not_called()

    def test_callbacks_keep_pointer_sized_results(self):
        self.assertEqual(ctypes.sizeof(screensaver.WNDPROC._restype_), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(screensaver.u.DefWindowProcW.restype, ctypes.c_ssize_t)


if __name__ == '__main__':
    unittest.main()
