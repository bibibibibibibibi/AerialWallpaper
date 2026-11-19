# -*- coding: utf-8 -*-
"""Render the real native board without running any live application services.

    python preview_preferences.py --library "X:\\动态壁纸视频"
    python preview_preferences.py --state search-empty --output preview-empty.png

The optional library is READ ONLY: video names and existing JPG thumbnails are
used, but configuration, playback, scheduled tasks and the registry are mocked.
Pillow is a development-only dependency for saving the window capture.
"""
import argparse
import math
import os
import tempfile

from preferences_panel import scan_library
from test_preferences_panel import FakeServices, PanelHarness, capture_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", help="Optional existing video directory (read only)")
    parser.add_argument("--video", help="Preferred video basename in that directory")
    parser.add_argument("--output", default="docs/screenshots/preferences-board.png")
    parser.add_argument("--scale", type=float, default=1.5, help="Requested logical scale; still fits the monitor work area")
    parser.add_argument("--state", choices=("paused", "playing", "stopped", "continuous", "empty", "search-empty", "dirty", "error"), default="paused")
    args = parser.parse_args()
    if not math.isfinite(args.scale) or args.scale <= 0:
        parser.error("--scale must be a positive finite number")
    if args.library and not os.path.isdir(args.library):
        parser.error("--library must name an existing directory")
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aerial-preview-") as temporary:
        directory = os.path.abspath(args.library) if args.library else temporary
        if not args.library and args.state != "empty":
            for name in ("Coastline_04_4K.mov", "Forest_Morning_4K.mov", "Palau_Coral_70_4K.mov"):
                with open(os.path.join(temporary, name), "wb"):
                    pass
        if args.state == "empty":
            directory = temporary
        items = scan_library(directory, FakeServices.VIDEO_EXT)
        preferred = args.video or "Palau_Coral_70_4K.mov"
        # Pin even an empty preview to its own existing directory: an empty
        # config would otherwise fall back to the machine's real default library.
        fallback_video = items[0][1] if items else os.path.join(directory, "__no_video__.mov")
        video = next((i[1] for i in items if os.path.basename(i[1]) == preferred), fallback_video)
        services = FakeServices(directory, video)
        services.cfg["always_play"] = args.state == "continuous"
        services.running = args.state not in ("stopped", "empty")
        services.paused = args.state not in ("playing", "continuous")
        with PanelHarness(services) as harness:
            harness.invoke(lambda: harness.panel._layout(args.scale))
            if args.state == "search-empty":
                harness.text("search", "没有匹配的风景")
            elif args.state in ("dirty", "error"):
                harness.text("burst", "15")
                if args.state == "error":
                    services.fail_update = True
                    harness.click("save")
            harness.invoke(harness.panel.refresh)
            # PrintWindow draws the actual parent and child HWNDs, not a mockup.
            harness.invoke(lambda: capture_window(harness.panel.hwnd, output))
            if harness.panel.errors:
                raise AssertionError(harness.panel.errors)
        print("Saved isolated native preview:", output)
        print("No wallpaper, configuration, scheduled task or screensaver settings changed.")


if __name__ == "__main__":
    main()
