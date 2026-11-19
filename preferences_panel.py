# -*- coding: utf-8 -*-
"""Aerial's native preferences board.

All system integration goes through the supplied services object (normally app).
Workers only return data; HWNDs, drawing and preference edits belong to the UI
thread. This boundary also lets the real controls be tested without changing a
wallpaper, scheduled task, screensaver, or the user's configuration.
"""
import ctypes
import os
import queue
import threading
import time
import traceback
from ctypes import wintypes as wt

from preferences import (
    BOARD_HEIGHT, BOARD_WIDTH, BURST_PRESETS, ConfigError, burst_seconds,
    fit_board, friendly_name, same_path, validate_burst,
)
from video_categories import (
    SCENERY_CATEGORIES, CATEGORY_LABELS, category_counts, classify_video, filter_scenery,
)

# Stable IDs: existing external panel automation keeps working.
ID_PLAY, ID_STOP, ID_SAVE = 1001, 1003, 1005
ID_AUTO, ID_BURST, ID_ALWAYS, ID_INSTALL = 1006, 1007, 1008, 1009
ID_LIBLIST, ID_LIBUSE, ID_MINIMIZE, ID_CLOSE = 1010, 1011, 1012, 1013
ID_SEARCH, ID_REFRESH, ID_LESS, ID_MORE, ID_BROWSE = 1014, 1015, 1016, 1017, 1018
PRESET_IDS = dict(zip(range(1020, 1024), BURST_PRESETS))
CATEGORY_IDS = dict(zip(range(1030, 1030 + len(SCENERY_CATEGORIES)),
                        (key for key, _label in SCENERY_CATEGORIES)))

WM_PAINT, WM_CLOSE, WM_DESTROY, WM_ERASEBKGND = 0xF, 0x10, 2, 0x14
WM_COMMAND, WM_TIMER, WM_DRAWITEM, WM_MEASUREITEM = 0x111, 0x113, 0x2B, 0x2C
WM_MOUSEMOVE, WM_MOUSELEAVE, WM_NCHITTEST = 0x200, 0x2A3, 0x84
WM_CTLCOLORSTATIC, WM_CTLCOLOREDIT, WM_CTLCOLORLISTBOX = 0x138, 0x133, 0x134
WM_KEYDOWN, WM_SETFONT, WM_DPICHANGED = 0x100, 0x30, 0x2E0
WM_PRINTCLIENT, WM_APP_RESULT = 0x318, 0x8001
BM_GETCHECK, BM_SETCHECK, BM_CLICK = 0xF0, 0xF1, 0xF5
LB_ADDSTRING, LB_RESETCONTENT, LB_SETCURSEL, LB_GETCURSEL = 0x180, 0x184, 0x186, 0x188
WS_CHILD, WS_VISIBLE, WS_TABSTOP = 0x40000000, 0x10000000, 0x10000
WS_POPUP, WS_SYSMENU, WS_MINIMIZEBOX, WS_CLIPCHILDREN = 0x80000000, 0x80000, 0x20000, 0x2000000
BS_OWNERDRAW = 0xB
TITLE_HEIGHT = 72


def rgb(value):
    """Hex RGB -> Win32 COLORREF (keeps the palette readable)."""
    value = int(value.lstrip("#"), 16)
    return ((value & 255) << 16) | (value & 0xFF00) | ((value >> 16) & 255)


BG, CARD, INK = rgb("F3F5F5"), rgb("FFFFFF"), rgb("192E32")
MUTED, BORDER, INPUT = rgb("6D8084"), rgb("E0E7E7"), rgb("F4F7F7")
ACCENT, ACCENT_HOVER, ACCENT_DOWN = rgb("147D73"), rgb("198E82"), rgb("0E645D")
TINT, SOFT_INK, DISABLED = rgb("E8F4F0"), rgb("335B57"), rgb("9CACAD")
NAVY, WHITE, DANGER = rgb("18343C"), rgb("FFFFFF"), rgb("BC454B")
AMBER, AMBER_BG, FOCUS = rgb("966516"), rgb("FBF3E3"), rgb("76ADA6")

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
H = wt.HWND
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, H, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


class DRAWITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wt.UINT), ("CtlID", wt.UINT), ("itemID", wt.UINT),
                ("itemAction", wt.UINT), ("itemState", wt.UINT),
                ("hwndItem", H), ("hDC", wt.HDC), ("rcItem", wt.RECT),
                ("itemData", ctypes.c_size_t)]


class MEASUREITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wt.UINT), ("CtlID", wt.UINT), ("itemID", wt.UINT),
                ("itemWidth", wt.UINT), ("itemHeight", wt.UINT),
                ("itemData", ctypes.c_size_t)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwFlags", wt.DWORD),
                ("hwndTrack", H), ("dwHoverTime", wt.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def _api(dll, name, restype, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, list(args)
    return fn


# Explicit pointer-sized signatures matter on 64-bit Windows.
_api(u, "DefWindowProcW", LRESULT, H, wt.UINT, wt.WPARAM, wt.LPARAM)
_api(u, "CallWindowProcW", LRESULT, ctypes.c_void_p, H, wt.UINT, wt.WPARAM, wt.LPARAM)
_api(u, "SetWindowLongPtrW", ctypes.c_ssize_t, H, ctypes.c_int, ctypes.c_ssize_t)
_api(u, "CreateWindowExW", H, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
_api(u, "RegisterClassW", wt.ATOM, ctypes.POINTER(WNDCLASS))
_api(u, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
_api(u, "FindWindowW", H, wt.LPCWSTR, wt.LPCWSTR)
_api(u, "SendMessageW", LRESULT, H, wt.UINT, wt.WPARAM, wt.LPARAM)
_api(u, "PostMessageW", wt.BOOL, H, wt.UINT, wt.WPARAM, wt.LPARAM)
_api(u, "SetWindowTextW", wt.BOOL, H, wt.LPCWSTR)
_api(u, "GetWindowTextW", ctypes.c_int, H, wt.LPWSTR, ctypes.c_int)
_api(u, "GetWindowTextLengthW", ctypes.c_int, H)
_api(u, "GetDlgCtrlID", ctypes.c_int, H)
_api(u, "EnableWindow", wt.BOOL, H, wt.BOOL)
_api(u, "IsWindowEnabled", wt.BOOL, H)
_api(u, "IsWindow", wt.BOOL, H)
_api(u, "IsWindowVisible", wt.BOOL, H)
_api(u, "ShowWindow", wt.BOOL, H, ctypes.c_int)
_api(u, "SetForegroundWindow", wt.BOOL, H)
_api(u, "SetWindowPos", wt.BOOL, H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
_api(u, "MoveWindow", wt.BOOL, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.BOOL)
_api(u, "InvalidateRect", wt.BOOL, H, ctypes.POINTER(wt.RECT), wt.BOOL)
_api(u, "GetClientRect", wt.BOOL, H, ctypes.POINTER(wt.RECT))
_api(u, "BeginPaint", wt.HDC, H, ctypes.POINTER(PAINTSTRUCT))
_api(u, "EndPaint", wt.BOOL, H, ctypes.POINTER(PAINTSTRUCT))
_api(u, "FillRect", ctypes.c_int, wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH)
_api(u, "DrawTextW", ctypes.c_int, wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT)
_api(u, "DrawFocusRect", wt.BOOL, wt.HDC, ctypes.POINTER(wt.RECT))
_api(u, "TrackMouseEvent", wt.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
_api(u, "ScreenToClient", wt.BOOL, H, ctypes.POINTER(wt.POINT))
_api(u, "SetWindowRgn", ctypes.c_int, H, wt.HRGN, wt.BOOL)
_api(u, "DestroyWindow", wt.BOOL, H)
_api(u, "PostQuitMessage", None, ctypes.c_int)
_api(u, "GetMessageW", wt.BOOL, ctypes.POINTER(wt.MSG), H, wt.UINT, wt.UINT)
_api(u, "TranslateMessage", wt.BOOL, ctypes.POINTER(wt.MSG))
_api(u, "DispatchMessageW", LRESULT, ctypes.POINTER(wt.MSG))
_api(u, "IsDialogMessageW", wt.BOOL, H, ctypes.POINTER(wt.MSG))
_api(u, "GetFocus", H)
_api(u, "SetFocus", H, H)
_api(u, "GetKeyState", ctypes.c_short, ctypes.c_int)
_api(u, "SetTimer", ctypes.c_size_t, H, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
_api(u, "KillTimer", wt.BOOL, H, ctypes.c_size_t)
_api(u, "GetDC", wt.HDC, H)
_api(u, "ReleaseDC", ctypes.c_int, H, wt.HDC)
_api(u, "GetForegroundWindow", H)
_api(u, "MonitorFromWindow", wt.HMONITOR, H, wt.DWORD)
_api(u, "GetMonitorInfoW", wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFO))
_api(u, "LoadCursorW", wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p)
_api(u, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
_api(u, "DestroyIcon", wt.BOOL, wt.HICON)
_api(g, "CreateSolidBrush", wt.HBRUSH, wt.COLORREF)
_api(g, "CreatePen", wt.HPEN, ctypes.c_int, ctypes.c_int, wt.COLORREF)
_api(g, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_api(g, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
_api(g, "GetStockObject", wt.HGDIOBJ, ctypes.c_int)
_api(g, "CreateRoundRectRgn", wt.HRGN, *([ctypes.c_int] * 6))
_api(g, "RoundRect", wt.BOOL, wt.HDC, *([ctypes.c_int] * 6))
_api(g, "MoveToEx", wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_api(g, "LineTo", wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int)
_api(g, "SetBkMode", ctypes.c_int, wt.HDC, ctypes.c_int)
_api(g, "SetBkColor", wt.COLORREF, wt.HDC, wt.COLORREF)
_api(g, "SetTextColor", wt.COLORREF, wt.HDC, wt.COLORREF)
_api(g, "CreateFontW", wt.HFONT, *([ctypes.c_int] * 13), wt.LPCWSTR)
_api(g, "CreateCompatibleDC", wt.HDC, wt.HDC)
_api(g, "CreateCompatibleBitmap", wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
_api(g, "DeleteDC", wt.BOOL, wt.HDC)
_api(g, "BitBlt", wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)
_api(g, "GetDeviceCaps", ctypes.c_int, wt.HDC, ctypes.c_int)
_api(k, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)


class _GdiInput(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("callback", ctypes.c_void_p),
                ("no_thread", wt.BOOL), ("no_codecs", wt.BOOL)]


try:
    gdip = ctypes.WinDLL("gdiplus")
    ptr = ctypes.c_void_p
    _api(gdip, "GdiplusStartup", ctypes.c_int, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(_GdiInput), ptr)
    _api(gdip, "GdipCreateFromHDC", ctypes.c_int, wt.HDC, ctypes.POINTER(ptr))
    _api(gdip, "GdipDeleteGraphics", ctypes.c_int, ptr)
    _api(gdip, "GdipSetSmoothingMode", ctypes.c_int, ptr, ctypes.c_int)
    _api(gdip, "GdipSetInterpolationMode", ctypes.c_int, ptr, ctypes.c_int)
    _api(gdip, "GdipCreatePath", ctypes.c_int, ctypes.c_int, ctypes.POINTER(ptr))
    _api(gdip, "GdipAddPathArc", ctypes.c_int, ptr, *([ctypes.c_float] * 6))
    _api(gdip, "GdipClosePathFigure", ctypes.c_int, ptr)
    _api(gdip, "GdipDeletePath", ctypes.c_int, ptr)
    _api(gdip, "GdipSetClipPath", ctypes.c_int, ptr, ptr, ctypes.c_int)
    _api(gdip, "GdipCreateSolidFill", ctypes.c_int, ctypes.c_int32, ctypes.POINTER(ptr))
    _api(gdip, "GdipDeleteBrush", ctypes.c_int, ptr)
    _api(gdip, "GdipFillPath", ctypes.c_int, ptr, ptr, ptr)
    _api(gdip, "GdipCreatePen1", ctypes.c_int, ctypes.c_int32, ctypes.c_float, ctypes.c_int, ctypes.POINTER(ptr))
    _api(gdip, "GdipDeletePen", ctypes.c_int, ptr)
    _api(gdip, "GdipDrawPath", ctypes.c_int, ptr, ptr, ptr)
    _api(gdip, "GdipLoadImageFromFile", ctypes.c_int, wt.LPCWSTR, ctypes.POINTER(ptr))
    _api(gdip, "GdipDisposeImage", ctypes.c_int, ptr)
    _api(gdip, "GdipGetImageWidth", ctypes.c_int, ptr, ctypes.POINTER(wt.UINT))
    _api(gdip, "GdipGetImageHeight", ctypes.c_int, ptr, ctypes.POINTER(wt.UINT))
    _api(gdip, "GdipDrawImageRectI", ctypes.c_int, ptr, ptr, *([ctypes.c_int] * 4))
    _api(gdip, "GdipCreateLineBrushFromRectI", ctypes.c_int, ctypes.POINTER(wt.RECT),
         ctypes.c_int32, ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ptr))
    _api(gdip, "GdipFillRectangleI", ctypes.c_int, ptr, ptr, *([ctypes.c_int] * 4))
    _gdi_token = ctypes.c_size_t()
    _gdi_input = _GdiInput(1, None, False, False)
    GDI_OK = gdip.GdiplusStartup(ctypes.byref(_gdi_token), ctypes.byref(_gdi_input), None) == 0
except (OSError, AttributeError):
    gdip, GDI_OK = None, False


def _argb(color, alpha=255):
    return ctypes.c_int32((alpha << 24) | ((color & 255) << 16) |
                         (color & 0xFF00) | ((color >> 16) & 255)).value


def _path(x, y, w, h, radius):
    path = ctypes.c_void_p()
    if gdip.GdipCreatePath(0, ctypes.byref(path)) or not path.value:
        return path
    d = min(radius * 2, w, h)
    for ax, ay, start in ((x, y, 180), (x + w - d, y, 270),
                          (x + w - d, y + h - d, 0), (x, y + h - d, 90)):
        gdip.GdipAddPathArc(path, ax, ay, d, d, start, 90)
    gdip.GdipClosePathFigure(path)
    return path


def rounded(hdc, x, y, w, h, radius, fill=None, edge=None):
    if w <= 0 or h <= 0:
        return
    if not GDI_OK:
        brush = g.CreateSolidBrush(fill) if fill is not None else g.GetStockObject(5)
        pen = g.CreatePen(0, 1, edge) if edge is not None else g.GetStockObject(8)
        old_brush, old_pen = g.SelectObject(hdc, brush), g.SelectObject(hdc, pen)
        g.RoundRect(hdc, int(x), int(y), int(x + w), int(y + h), int(radius * 2), int(radius * 2))
        g.SelectObject(hdc, old_pen)
        g.SelectObject(hdc, old_brush)
        if fill is not None:
            g.DeleteObject(brush)
        if edge is not None:
            g.DeleteObject(pen)
        return
    gfx, brush, pen = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    path = None
    try:
        if gdip.GdipCreateFromHDC(hdc, ctypes.byref(gfx)):
            return
        gdip.GdipSetSmoothingMode(gfx, 4)
        path = _path(float(x) + .5, float(y) + .5, float(w) - 1, float(h) - 1, radius)
        if not path.value:
            return
        if fill is not None and not gdip.GdipCreateSolidFill(_argb(fill), ctypes.byref(brush)):
            gdip.GdipFillPath(gfx, brush, path)
        if edge is not None and not gdip.GdipCreatePen1(_argb(edge), 1.0, 2, ctypes.byref(pen)):
            gdip.GdipDrawPath(gfx, pen, path)
    finally:
        if pen.value:
            gdip.GdipDeletePen(pen)
        if brush.value:
            gdip.GdipDeleteBrush(brush)
        if path and path.value:
            gdip.GdipDeletePath(path)
        if gfx.value:
            gdip.GdipDeleteGraphics(gfx)


def line(hdc, x1, y1, x2, y2, color, width=1):
    pen = g.CreatePen(0, max(1, int(width)), color)
    old = g.SelectObject(hdc, pen)
    g.MoveToEx(hdc, int(x1), int(y1), None)
    g.LineTo(hdc, int(x2), int(y2))
    g.SelectObject(hdc, old)
    g.DeleteObject(pen)


def text(hdc, value, rect, font, color=INK, align="left"):
    x, y, w, h = rect
    rc = wt.RECT(int(x), int(y), int(x + w), int(y + h))
    g.SetBkMode(hdc, 1)
    g.SetTextColor(hdc, color)
    old = g.SelectObject(hdc, font)
    # NOPREFIX preserves '&' in filenames; ellipsis avoids bleeding into actions.
    flags = 0x20 | 0x4 | 0x800 | 0x8000 | {"left": 0, "center": 1, "right": 2}[align]
    u.DrawTextW(hdc, str(value), -1, ctypes.byref(rc), flags)
    g.SelectObject(hdc, old)


def get_text(hwnd):
    n = u.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(max(1, n))
    u.GetWindowTextW(hwnd, buf, n)
    return buf.value


def video_directory(cfg, app_dir):
    value = cfg.get("video")
    if isinstance(value, str) and value.strip():
        directory = os.path.dirname(os.path.abspath(value))
        if os.path.isdir(directory):
            return directory
    fallback = "X:\\动态壁纸视频"
    return fallback if os.path.isdir(fallback) else app_dir


def scan_library(directory, extensions):
    items = []
    # scandir doesn't stat every non-video file (important for large libraries).
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.lower().endswith(extensions) and entry.is_file():
                base = os.path.splitext(entry.name)[0]
                candidates = (os.path.join(directory, ".thumbs", base + ".jpg"),
                              os.path.join(directory, base + ".poster.jpg"))
                thumbnail = next((p for p in candidates if os.path.isfile(p)), "")
                items.append((friendly_name(entry.path), os.path.abspath(entry.path), thumbnail))
    return sorted(items, key=lambda item: item[0].casefold())


class OPENFILENAME(ctypes.Structure):
    _fields_ = [("lStructSize", wt.DWORD), ("hwndOwner", H), ("hInstance", wt.HINSTANCE),
                ("lpstrFilter", wt.LPCWSTR), ("lpstrCustomFilter", wt.LPWSTR),
                ("nMaxCustFilter", wt.DWORD), ("nFilterIndex", wt.DWORD),
                ("lpstrFile", wt.LPWSTR), ("nMaxFile", wt.DWORD),
                ("lpstrFileTitle", wt.LPWSTR), ("nMaxFileTitle", wt.DWORD),
                ("lpstrInitialDir", wt.LPCWSTR), ("lpstrTitle", wt.LPCWSTR),
                ("Flags", wt.DWORD), ("nFileOffset", wt.WORD), ("nFileExtension", wt.WORD),
                ("lpstrDefExt", wt.LPCWSTR), ("lCustData", wt.LPARAM),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wt.LPCWSTR),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", wt.DWORD), ("FlagsEx", wt.DWORD)]


def choose_video(owner, directory):
    """User-initiated native picker; never changes the process working directory."""
    dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
    show = _api(dialog, "GetOpenFileNameW", wt.BOOL, ctypes.POINTER(OPENFILENAME))
    last_error = _api(dialog, "CommDlgExtendedError", wt.DWORD)
    filename = ctypes.create_unicode_buffer(32768)
    options = OPENFILENAME()
    options.lStructSize, options.hwndOwner = ctypes.sizeof(options), owner
    options.lpstrFile, options.nMaxFile = ctypes.cast(filename, wt.LPWSTR), len(filename)
    options.lpstrFilter = "视频文件\0*.mp4;*.mov;*.webm;*.mkv;*.m4v\0\0"
    options.lpstrInitialDir = directory
    options.lpstrTitle = "选择并应用本地视频壁纸"
    options.Flags = 0x80000 | 0x1000 | 0x800 | 0x8 | 0x4 | 0x2000000
    if show(ctypes.byref(options)):
        return filename.value
    error = last_error()
    if error:
        raise OSError("无法打开视频选择器（0x%X）" % error)
    return ""


class PreferencesPanel:
    """One UI instance; injected services are the only system-changing boundary."""

    def __init__(self, services, window_class="AerialWallpaperUI", ready=None):
        self.services, self.window_class, self.ready = services, window_class, ready
        self.hwnd = None
        self.scale, self.width, self.height = 1.0, BOARD_WIDTH, BOARD_HEIGHT
        self.controls, self.positions, self.old_procs = {}, {}, {}
        self.fonts, self.brushes, self.checks = {}, {}, {}
        self.items, self.visible, self.selection = [], [], -1
        self.category = "all"
        self.cfg, self.config_error, self.errors = {}, "", []
        self.auto_actual, self.running, self.always = None, False, False
        self.dirty, self.setting_text, self.closed = False, False, False
        self.initializing = True
        self.hover, self.flash_message, self.flash_until = None, "", 0
        self.flash_error = False
        self.results, self.jobs, self.job_serial = queue.Queue(), {}, 0
        self.image, self.image_path = ctypes.c_void_p(), ""
        self.icon = None
        self._proc = WNDPROC(self._wndproc)
        self._button_proc = WNDPROC(self._button_wndproc)

    def px(self, value):
        return int(round(value * self.scale))

    def rect(self, value):
        return tuple(self.px(v) for v in value)

    def label(self, hdc, value, rect, role="body", color=INK, align="left"):
        text(hdc, value, self.rect(rect), self.fonts[role], color, align)

    def box(self, hdc, rect, radius=12, fill=CARD, edge=BORDER):
        rounded(hdc, *self.rect(rect), self.px(radius), fill, edge)

    def separator(self, hdc, x1, y, x2):
        line(hdc, self.px(x1), self.px(y), self.px(x2), self.px(y), BORDER)

    def invalidate(self, rect=None):
        if self.hwnd and not self.closed:
            rc = wt.RECT(*rect) if rect else None
            u.InvalidateRect(self.hwnd, ctypes.byref(rc) if rc else None, False)

    def set_text(self, key, value):
        hwnd = self.controls.get(key)
        if hwnd and get_text(hwnd) != str(value):
            u.SetWindowTextW(hwnd, str(value))

    def enable(self, key, enabled):
        hwnd = self.controls.get(key)
        if hwnd and bool(u.IsWindowEnabled(hwnd)) != bool(enabled):
            u.EnableWindow(hwnd, bool(enabled))
            u.InvalidateRect(hwnd, None, False)

    def flash(self, message, error=False, seconds=6):
        self.flash_message, self.flash_error = str(message), error
        self.flash_until = time.monotonic() + seconds
        self.invalidate()

    def _read_config(self):
        try:
            candidate = self.services.load_cfg()
            if not isinstance(candidate, dict):
                raise ConfigError("配置必须是 JSON 对象")
            self.cfg = candidate
            self.config_error = ""
        except (ConfigError, OSError, ValueError) as exc:
            self.config_error = "配置读取失败，未覆盖原文件：" + str(exc)
        return self.cfg

    def _commit(self, **changes):
        # Always merge against a fresh read, not a possibly stale UI snapshot.
        self.cfg = self.services.update_cfg(**changes)
        self.config_error = ""

    def _work_area(self, hwnd=None):
        monitor = u.MonitorFromWindow(hwnd or u.GetForegroundWindow(), 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.rcWork

    def _make_fonts(self):
        old_fonts = self.fonts
        specs = {"brand": (23, True, "Segoe UI"), "heading": (16, True, "Microsoft YaHei UI"),
                 "body": (13, False, "Microsoft YaHei UI"), "small": (11, False, "Microsoft YaHei UI"),
                 "hero": (21, True, "Microsoft YaHei UI"), "number": (15, True, "Segoe UI")}
        self.fonts = {role: g.CreateFontW(-max(1, self.px(size)), 0, 0, 0, 600 if bold else 400,
                                        0, 0, 0, 1, 0, 0, 5, 0, family)
                      for role, (size, bold, family) in specs.items()}
        for key, hwnd in self.controls.items():
            font = self.fonts["number" if key == "burst" else "body"]
            u.SendMessageW(hwnd, WM_SETFONT, font, 1)
        for font in old_fonts.values():
            g.DeleteObject(font)

    def _layout(self, dpi_scale, suggested=None):
        work = self._work_area(self.hwnd)
        self.scale = fit_board(work.right - work.left, work.bottom - work.top, dpi_scale)
        self.width, self.height = self.px(BOARD_WIDTH), self.px(BOARD_HEIGHT)
        if suggested is None:
            x = work.left + (work.right - work.left - self.width) // 2
            y = work.top + (work.bottom - work.top - self.height) // 2
        else:
            x = min(max(work.left, suggested.left), work.right - self.width)
            y = min(max(work.top, suggested.top), work.bottom - self.height)
        self._make_fonts()
        if self.hwnd:
            u.SetWindowPos(self.hwnd, None, x, y, self.width, self.height, 0x14)
            # Set fixed row height before MoveWindow applies integral-height
            # rounding; doing this afterwards clips a row when DPI decreases.
            if self.controls.get("library"):
                u.SendMessageW(self.controls["library"], 0x1A0, 0, self.px(32))
            for key, rect in self.positions.items():
                u.MoveWindow(self.controls[key], *self.rect(rect), True)
            region = g.CreateRoundRectRgn(0, 0, self.width + 1, self.height + 1, self.px(22), self.px(22))
            if not u.SetWindowRgn(self.hwnd, region, True):
                g.DeleteObject(region)
        self.invalidate()
        return x, y

    def _control(self, key, cls, title, cid, rect, style=0, tab=True):
        flags = WS_CHILD | WS_VISIBLE | style | (WS_TABSTOP if tab else 0)
        hwnd = u.CreateWindowExW(0, cls, title, flags, *self.rect(rect), self.hwnd, cid, self.hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.controls[key], self.positions[key] = hwnd, rect
        font = self.fonts["number" if key == "burst" else "body"]
        u.SendMessageW(hwnd, WM_SETFONT, font, 0)
        if cls == "BUTTON":
            old = u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self._button_proc, ctypes.c_void_p).value)
            self.old_procs[hwnd] = old
        return hwnd

    def _button(self, key, title, cid, rect, tab=True):
        return self._control(key, "BUTTON", title, cid, rect, BS_OWNERDRAW, tab)

    def _create_controls(self):
        # Creation order is also keyboard Tab order.
        self._button("play", "立即动一下", ID_PLAY, (44, 350, 144, 42))
        self._button("toggle", "启动壁纸", ID_STOP, (200, 350, 136, 42))
        self._button("apply", "使用此视频", ID_LIBUSE, (456, 350, 156, 42))
        self._button("browse", "选择视频", ID_BROWSE, (254, 444, 78, 34))
        self._control("search", "EDIT", "", ID_SEARCH, (378, 452, 176, 23), 0x80)
        cue = ctypes.create_unicode_buffer("搜索视频名称…")
        u.SendMessageW(self.controls["search"], 0x1501, 1, ctypes.addressof(cue))
        self._button("refresh", "刷新视频库", ID_REFRESH, (580, 444, 32, 34))
        for i, (cid, category) in enumerate(CATEGORY_IDS.items()):
            self._button("category_" + category, CATEGORY_LABELS[category] + " 0", cid,
                         (36 + i * 65, 487, 60, 28))
            self.checks[self.controls["category_" + category]] = category == self.category
        self._control("library", "ListBox", "", ID_LIBLIST, (36, 526, 584, 128),
                      0x200000 | 0x1 | 0x40 | 0x10)
        self._button("always", "持续播放", ID_ALWAYS, (970, 164, 48, 28))
        self._button("less", "减少一秒", ID_LESS, (674, 313, 34, 32))
        self._control("burst", "EDIT", str(burst_seconds(self.cfg.get("burst_seconds"))),
                      ID_BURST, (714, 316, 54, 26), 0x2000 | 0x80 | 0x1)
        u.SendMessageW(self.controls["burst"], 0xC5, 2, 0)  # EM_LIMITTEXT
        self._button("more", "增加一秒", ID_MORE, (778, 313, 34, 32))
        self._button("save", "已保存", ID_SAVE, (828, 308, 192, 42))
        for i, (cid, value) in enumerate(PRESET_IDS.items()):
            self._button("preset_" + str(value), str(value) + " 秒", cid, (672 + i * 90, 364, 78, 30))
        self._button("auto", "登录时自动运行", ID_AUTO, (970, 526, 48, 28))
        self._button("install", "安装屏保", ID_INSTALL, (904, 606, 116, 38))
        self._button("minimize", "最小化", ID_MINIMIZE, (962, 20, 32, 32), False)
        self._button("close", "关闭偏好设置", ID_CLOSE, (1004, 20, 32, 32), False)
        self.checks[self.controls["always"]] = bool(self.cfg.get("always_play"))
        self.checks[self.controls["auto"]] = False

    def start_job(self, kind, function):
        if kind in self.jobs or self.closed:
            return False
        self.job_serial += 1
        token = self.job_serial
        self.jobs[kind] = token
        self._update_controls()

        def work():
            try:
                result, error = function(), None
            except Exception as exc:
                result, error = None, str(exc) or type(exc).__name__
            if not self.closed:
                self.results.put((kind, token, result, error))
                u.PostMessageW(self.hwnd, WM_APP_RESULT, 0, 0)

        threading.Thread(target=work, name="aerial-" + kind, daemon=True).start()
        return True

    def _drain_results(self):
        while True:
            try:
                kind, token, result, error = self.results.get_nowait()
            except queue.Empty:
                break
            if self.jobs.get(kind) != token:
                continue
            del self.jobs[kind]
            if error:
                if kind in ("auto_check", "auto_change"):
                    self.auto_actual = None
                    if kind == "auto_change":
                        self.start_job("auto_check", self.services.task_installed)
                if kind == "library":
                    self.flash("视频库读取失败：" + error, True)
                else:
                    self.flash("操作未完成：" + error, True)
            elif kind == "library":
                old_item = self.selected_item()
                self.items = result
                self._rebuild_list(old_item[1] if old_item else self.cfg.get("video", ""))
            elif kind in ("auto_check", "auto_change"):
                self.auto_actual = bool(result)
                if kind == "auto_change":
                    self.flash("登录时自动运行已" + ("开启" if result else "关闭"))
            elif kind == "screensaver":
                self.flash("屏保已安装 · 可在 Windows 屏保设置中调整等待时间和恢复登录选项", seconds=8)
            elif kind == "player":
                self.flash(result)
        self.refresh()

    def rescan(self):
        directory = video_directory(self.cfg, self.services.APP_DIR)
        self.start_job("library", lambda: scan_library(directory, self.services.VIDEO_EXT))

    def selected_item(self):
        if 0 <= self.selection < len(self.visible):
            index = self.visible[self.selection]
            if 0 <= index < len(self.items):
                return self.items[index]
        return None

    def _rebuild_list(self, preferred=""):
        counts = category_counts(self.items)
        for category, label in SCENERY_CATEGORIES:
            key = "category_" + category
            self.set_text(key, "%s %s" % (label, counts[category]))
            control = self.controls[key]
            self.checks[control] = category == self.category
            u.InvalidateRect(control, None, False)
        self.visible = filter_scenery(self.items, get_text(self.controls["search"]), self.category)
        hwnd = self.controls["library"]
        u.SendMessageW(hwnd, 0xB, 0, 0)  # WM_SETREDRAW
        try:
            u.SendMessageW(hwnd, LB_RESETCONTENT, 0, 0)
            self.selection = -1
            for row, index in enumerate(self.visible):
                item = self.items[index]
                buf = ctypes.create_unicode_buffer(item[0])
                u.SendMessageW(hwnd, LB_ADDSTRING, 0, ctypes.addressof(buf))
                if same_path(item[1], preferred):
                    self.selection = row
            if self.visible:
                if self.selection < 0:
                    self.selection = 0
                u.SendMessageW(hwnd, LB_SETCURSEL, self.selection, 0)
        finally:
            u.SendMessageW(hwnd, 0xB, 1, 0)
        u.InvalidateRect(hwnd, None, True)
        self._preview_changed()

    def _preview_changed(self):
        self._dispose_image()
        self._update_controls()
        self.invalidate()

    def _dispose_image(self):
        if self.image.value and GDI_OK:
            gdip.GdipDisposeImage(self.image)
        self.image, self.image_path = ctypes.c_void_p(), ""

    def _duration_text(self, value):
        self.setting_text = True
        try:
            self.set_text("burst", value)
        finally:
            self.setting_text = False
        self.dirty = str(value) != str(burst_seconds(self.cfg.get("burst_seconds")))
        self._update_controls()

    def save_duration(self, value=None):
        if self.always:
            return
        raw = get_text(self.controls["burst"]) if value is None else str(value)
        try:
            seconds = validate_burst(raw)
        except ValueError as exc:
            self.flash(str(exc), True)
            u.SetFocus(self.controls["burst"])
            u.SendMessageW(self.controls["burst"], 0xB1, 0, -1)
            return
        # Don't discard the draft on a disk/validation failure.
        self._commit(burst_seconds=seconds)
        self._duration_text(seconds)
        self.flash("已保存：回到桌面后播放 %s 秒 · 从下一轮播放开始使用" % seconds)

    def _toggle_auto(self):
        if "auto_check" in self.jobs or "auto_change" in self.jobs:
            return
        if self.auto_actual is None:
            self.flash("正在重新检查登录自启…")
            self.start_job("auto_check", self.services.task_installed)
            return
        want = not self.auto_actual

        def change():
            ok = self.services.install_task() if want else self.services.uninstall_task()
            if ok is False:
                raise RuntimeError("Windows 未能更新登录自启，请检查权限后重试")
            observed = self.services.task_installed()
            if bool(observed) != want:
                raise RuntimeError("登录自启状态未改变，请稍后重试")
            return observed

        self.flash("正在" + ("开启" if want else "关闭") + "登录自启…")
        self.start_job("auto_change", change)

    def _player_action(self, action):
        if "player" in self.jobs:
            return

        def work():
            if action == "stop":
                if not self.services.stop_player():
                    raise RuntimeError("播放器尚未停止，请重试")
                return "已停止壁纸 · 桌面保留当前画面"
            if action == "play" and self.services.is_running():
                if not self.services.signal_player(self.services.EVT_PLAY):
                    raise RuntimeError("播放器暂未响应，请稍后重试")
                return "已请求播放" + (" · 持续播放模式" if self.always else " · 本轮结束后自动定格")
            requested_at = time.time()
            if self.services.start_player() is False:
                raise RuntimeError("无法启动播放器")
            # The player owns its mutex well before mpv and control events are
            # ready. A newly written status is the actual initialization ack.
            deadline = time.monotonic() + 20
            while not self.closed and time.monotonic() < deadline:
                state = self.services.read_status() or {}
                try:
                    ready = float(state.get("ts", 0)) >= requested_at - .1
                except (TypeError, ValueError, OverflowError):
                    ready = False
                if ready and self.services.is_running():
                    return "壁纸已启动 · 播放偏好继续沿用"
                time.sleep(.1)
            raise RuntimeError("播放器暂未就绪，请检查视频和 mpv 路径")

        self.flash("正在停止壁纸…" if action == "stop" else "正在唤醒壁纸…")
        self.start_job("player", work)

    def apply_selection(self):
        if "player" in self.jobs:
            self.flash("播放器正在切换状态，请完成后再应用视频")
            return
        item = self.selected_item()
        if not item:
            return
        if not os.path.isfile(item[1]):
            self.flash("视频文件已移动或删除，请刷新视频库", True)
            return
        self._commit(video=os.path.abspath(item[1]), nudge=time.time())
        self.flash("已设为壁纸：" + item[0])
        if not self.services.is_running():
            self._player_action("start")
        self._update_controls()
        u.InvalidateRect(self.controls["library"], None, False)
        self.invalidate()

    def browse_video(self):
        if "player" in self.jobs or "library" in self.jobs:
            return
        path = choose_video(self.hwnd, video_directory(self.cfg, self.services.APP_DIR))
        if not path:
            return
        if not path.lower().endswith(self.services.VIDEO_EXT) or not os.path.isfile(path):
            self.flash("请选择存在的 MP4 / MOV / MKV / WEBM / M4V 视频", True)
            return
        self._commit(video=os.path.abspath(path), nudge=time.time())
        self.category = "all"
        self.set_text("search", "")
        self._rebuild_list()
        self.selection = -1
        u.SendMessageW(self.controls["library"], LB_SETCURSEL, -1, 0)
        self._preview_changed()
        self.rescan()
        self.flash("已设为壁纸：" + friendly_name(path))
        if not self.services.is_running():
            self._player_action("start")

    def _install_screensaver(self):
        def work():
            ok, info = self.services.install_screensaver()
            if not ok:
                raise RuntimeError(info)
            return info
        self.flash("正在安装屏幕保护程序…")
        self.start_job("screensaver", work)

    def _update_controls(self):
        if not self.controls:
            return
        auto_busy = "auto_check" in self.jobs or "auto_change" in self.jobs
        self.enable("auto", not auto_busy)
        self.checks[self.controls["auto"]] = bool(self.auto_actual)
        self.checks[self.controls["always"]] = self.always
        for key in ("always", "auto"):
            u.InvalidateRect(self.controls[key], None, False)
        self.enable("refresh", "library" not in self.jobs)
        u.ShowWindow(self.controls["library"], 5 if self.visible else 0)
        self.enable("install", "screensaver" not in self.jobs)
        self.set_text("install", "安装中…" if "screensaver" in self.jobs else "安装屏保")
        player_busy = "player" in self.jobs
        self.enable("always", not player_busy)
        self.enable("browse", not player_busy and "library" not in self.jobs)
        self.enable("play", not player_busy)
        self.enable("toggle", not player_busy)
        self.set_text("toggle", "请稍候…" if player_busy else ("停止壁纸" if self.running else "启动壁纸"))
        item = self.selected_item()
        is_current = bool(item and same_path(item[1], self.cfg.get("video", "")))
        self.enable("apply", bool(item) and not player_busy and (not is_current or not self.running))
        self.set_text("apply", "正在使用" if is_current and self.running else "使用此视频")
        for key in ("less", "burst", "more"):
            self.enable(key, not self.always)
        self.enable("save", not self.always and self.dirty)
        self.set_text("save", "持续播放中" if self.always else ("保存时长" if self.dirty else "已保存"))
        for value in BURST_PRESETS:
            key = "preset_" + str(value)
            self.enable(key, not self.always)
            u.InvalidateRect(self.controls[key], None, False)
        self.invalidate()

    def refresh(self):
        if self.closed:
            return
        previous_video = self.cfg.get("video", "")
        self._read_config()
        self.always = bool(self.cfg.get("always_play"))
        try:
            self.running = bool(self.services.is_running())
            self.status = self.services.read_status() or {}
            if not isinstance(self.status, dict):
                self.status = {}
        except Exception as exc:
            self.running, self.status = False, {}
            self.flash("无法读取播放器状态：" + str(exc), True)
        duration = burst_seconds(self.cfg.get("burst_seconds"))
        if not self.dirty and get_text(self.controls["burst"]) != str(duration):
            self._duration_text(duration)
        if not same_path(previous_video, self.cfg.get("video", "")):
            u.InvalidateRect(self.controls["library"], None, False)
        self._update_controls()

    def _status_caption(self):
        seconds = burst_seconds(self.cfg.get("burst_seconds"))
        if not self.running:
            return "未运行", "点击「启动壁纸」开始，关闭此面板不会停止壁纸。", AMBER, AMBER_BG
        if not self.status:
            return "启动中", "正在准备桌面画面…", MUTED, INPUT
        if self.status.get("paused"):
            detail = "亮屏或解锁后继续播放。" if self.always else "回到桌面时播放 %s 秒，其余时间保持静止。" % seconds
            return "已定格", detail, MUTED, INPUT
        return "正在播放", ("持续播放已开启，锁屏或熄屏时仍会暂停。" if self.always else
                          "正在播放，本轮结束后自动定格。"), ACCENT, TINT

    def _draw_preview(self, hdc):
        image_rect = (25, 97, 606, 230)
        self.box(hdc, image_rect, 12, NAVY, None)
        item = self.selected_item()
        path = item[2] if item else ""
        if not path and self.image_path:
            self._dispose_image()
        if GDI_OK and path:
            if self.image_path != path:
                self._dispose_image()
                self.image_path = path
                if gdip.GdipLoadImageFromFile(path, ctypes.byref(self.image)):
                    self.image = ctypes.c_void_p()
            if self.image.value:
                gfx, clip, shade = ctypes.c_void_p(), None, ctypes.c_void_p()
                try:
                    if not gdip.GdipCreateFromHDC(hdc, ctypes.byref(gfx)):
                        x, y, w, h = self.rect(image_rect)
                        clip = _path(x, y, w, h, self.px(12))
                        if clip.value:
                            gdip.GdipSetClipPath(gfx, clip, 0)
                        gdip.GdipSetInterpolationMode(gfx, 7)
                        iw, ih = wt.UINT(), wt.UINT()
                        gdip.GdipGetImageWidth(self.image, ctypes.byref(iw))
                        gdip.GdipGetImageHeight(self.image, ctypes.byref(ih))
                        factor = max(w / max(iw.value, 1), h / max(ih.value, 1))
                        dw, dh = int(iw.value * factor) + 1, int(ih.value * factor) + 1
                        gdip.GdipDrawImageRectI(gfx, self.image, x + (w - dw) // 2, y + (h - dh) // 2, dw, dh)
                        # A real alpha gradient keeps white titles readable on any scene.
                        sx, sy, sw, sh = self.rect((25, 207, 606, 120))
                        # GDI+ Rect uses X/Y/Width/Height, not Win32 right/bottom.
                        grad_rect = wt.RECT(sx, sy, sw, sh)
                        if not gdip.GdipCreateLineBrushFromRectI(ctypes.byref(grad_rect), _argb(NAVY, 0),
                                                               _argb(NAVY, 230), 1, 4, ctypes.byref(shade)):
                            gdip.GdipFillRectangleI(gfx, shade, sx, sy, sw, sh)
                finally:
                    if shade.value:
                        gdip.GdipDeleteBrush(shade)
                    if clip and clip.value:
                        gdip.GdipDeletePath(clip)
                    if gfx.value:
                        gdip.GdipDeleteGraphics(gfx)
        if not self.image.value:
            # Honest, intentional empty state instead of an unrelated stock photo.
            self.box(hdc, (56, 164, 44, 36), 10, SOFT_INK, None)
            for points in ((64, 190, 76, 176), (76, 176, 87, 190), (83, 185, 91, 177)):
                line(hdc, *(self.px(v) for v in points), rgb("A5C9C0"), self.px(2))
            self.label(hdc, "暂无缩略图" if item else "从一段风景开始", (114, 160, 400, 25), "heading", WHITE)
            self.label(hdc, "视频仍可正常使用" if item else "将视频放入片库，点击刷新即可发现", (114, 190, 400, 20), "small", rgb("B5CBCB"))
        self.box(hdc, (44, 115, 128, 24), 12, NAVY, None)
        self.label(hdc, "壁纸预览 · 静态画面", (44, 115, 128, 24), "small", WHITE, "center")
        if item:
            current = same_path(item[1], self.cfg.get("video", ""))
            if current:
                self.box(hdc, (526, 115, 86, 24), 12, TINT, None)
                self.label(hdc, "当前壁纸", (526, 115, 86, 24), "small", ACCENT, "center")
            self.label(hdc, item[0], (44, 262, 568, 30), "hero", WHITE)
            ext = os.path.splitext(item[1])[1][1:].upper()
            self.label(hdc, ext + ("  /  已应用" if current else "  /  预览中，点击「使用此视频」后生效"),
                       (44, 295, 568, 18), "small", rgb("DCEAE8"))

    def draw(self, hdc):
        rc = wt.RECT(0, 0, self.width, self.height)
        u.FillRect(hdc, ctypes.byref(rc), self.brushes["bg"])
        if not self.fonts:
            return
        # Header: a quiet brand, a real state indicator, native window controls.
        u.FillRect(hdc, ctypes.byref(wt.RECT(0, 0, self.width, self.px(TITLE_HEIGHT))), self.brushes["card"])
        self.separator(hdc, 0, 71, BOARD_WIDTH)
        self.box(hdc, (24, 20, 34, 34), 10, ACCENT, None)
        for points in ((32, 43, 40, 31), (40, 31, 50, 43), (45, 37, 49, 32)):
            line(hdc, *(self.px(v) for v in points), WHITE, self.px(2))
        self.label(hdc, "Aerial", (70, 15, 96, 30), "brand")
        self.label(hdc, "动态壁纸 / 偏好设置", (71, 45, 260, 17), "small", MUTED)
        badge, detail, badge_color, badge_bg = self._status_caption()
        self.box(hdc, (832, 23, 108, 28), 14, badge_bg, None)
        self.box(hdc, (845, 34, 6, 6), 3, badge_color, None)
        self.label(hdc, badge, (858, 23, 74, 28), "small", badge_color, "center")
        self.label(hdc, "属于你的桌面风景", (274, 25, 320, 24), "body", MUTED)

        self.box(hdc, (24, 96, 608, 316))
        self._draw_preview(hdc)
        self.box(hdc, (24, 428, 608, 240))
        self.label(hdc, "本地视频库", (44, 445, 108, 28), "heading")
        count = "%s 个视频" % len(self.items)
        if self.category != "all" or get_text(self.controls["search"]).strip():
            count = "%s / %s 个结果" % (len(self.visible), len(self.items))
        self.label(hdc, count, (154, 447, 94, 24), "small", MUTED)
        self.box(hdc, (344, 444, 224, 34), 8, INPUT)
        self.box(hdc, (356, 454, 11, 11), 6, None, MUTED)
        line(hdc, self.px(365), self.px(463), self.px(369), self.px(467), MUTED, self.px(1.4))
        if not self.visible:
            title = "正在读取视频库…" if "library" in self.jobs else ("没有匹配的视频" if self.items else "视频库还是空的")
            self.label(hdc, title, (44, 548, 568, 26), "body", MUTED, "center")
            hint = "切换分类、换个关键词，或清空搜索" if self.items else "点击「选择视频」打开本地视频，或放入视频后刷新"
            self.label(hdc, hint, (44, 578, 568, 24), "small", MUTED, "center")

        self.box(hdc, (652, 96, 388, 356))
        self.label(hdc, "播放偏好", (672, 114, 200, 28), "heading")
        self.box(hdc, (948, 118, 72, 22), 11, TINT if not self.always else INPUT, None)
        self.label(hdc, "持续模式" if self.always else "按需播放", (948, 118, 72, 22), "small", SOFT_INK, "center")
        self.label(hdc, "持续播放", (672, 164, 268, 28))
        self.label(hdc, "关闭后，播放几秒便自动定格。", (672, 195, 348, 20), "small", MUTED)
        self.separator(hdc, 672, 229, 1020)
        duration_color = DISABLED if self.always else INK
        self.label(hdc, "回到桌面后播放", (672, 244, 220, 24), color=duration_color)
        self.label(hdc, "3–30 秒", (924, 244, 96, 24), "small", MUTED, "right")
        self.label(hdc, "亮屏、解锁或屏保结束时触发", (672, 273, 348, 20), "small", MUTED)
        self.box(hdc, (672, 308, 144, 42), 8, INPUT, FOCUS if u.GetFocus() == self.controls["burst"] else BORDER)
        self.box(hdc, (672, 413, 348, 24), 6, TINT, None)
        self.box(hdc, (682, 422, 5, 5), 3, ACCENT, None)
        self.label(hdc, "锁屏、熄屏与屏保期间始终暂停", (696, 413, 312, 24), "small", SOFT_INK)

        self.box(hdc, (652, 468, 388, 200))
        self.label(hdc, "系统集成", (672, 485, 220, 28), "heading")
        self.label(hdc, "登录时自动运行", (672, 524, 280, 28))
        if "auto_check" in self.jobs:
            auto_caption = "正在检查 Windows 登录自启…"
        elif "auto_change" in self.jobs:
            auto_caption = "正在更新，请稍候…"
        elif self.auto_actual is None:
            auto_caption = "状态未知，点击开关重新检查"
        else:
            auto_caption = "已开启 · 仅当前账户" if self.auto_actual else "已关闭 · 仅当前账户"
        self.label(hdc, auto_caption, (672, 554, 348, 18), "small", MUTED)
        self.separator(hdc, 672, 587, 1020)
        self.label(hdc, "视频屏幕保护程序", (672, 604, 224, 24))
        self.label(hdc, "与壁纸共用本地视频库", (672, 631, 224, 18), "small", MUTED)

        # A dedicated feedback line never displaces a setting or a filename.
        if self.config_error:
            message, color = self.config_error, DANGER
        elif time.monotonic() < self.flash_until:
            message, color = self.flash_message, DANGER if self.flash_error else ACCENT
        elif self.dirty:
            message, color = "播放时长尚未保存 · 点击保存或按 Enter", AMBER
        else:
            message, color = detail, MUTED
        self.label(hdc, message, (24, 684, 812, 24), "small", color)
        self.label(hdc, "Ctrl + F  搜索视频", (848, 684, 192, 24), "small", MUTED, "right")

    def _draw_button(self, dis):
        hdc, hwnd, cid = dis.hDC, dis.hwndItem, dis.CtlID
        rc = dis.rcItem
        x, y, w, h = rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top
        enabled = bool(u.IsWindowEnabled(hwnd))
        pressed = bool(dis.itemState & 0x1)  # ODS_SELECTED, NOT ODS_FOCUS (0x10)
        hovered = self.hover == hwnd and enabled
        u.FillRect(hdc, ctypes.byref(rc), self.brushes["card"])
        if cid in (ID_AUTO, ID_ALWAYS):
            checked = self.checks.get(hwnd, False)
            fill = (ACCENT_HOVER if hovered else ACCENT) if checked else rgb("CFDADA")
            if not enabled:
                fill = rgb("B2CFCA") if checked else BORDER
            ty, th = y + self.px(2), h - self.px(4)
            rounded(hdc, x + 1, ty, w - 2, th, th / 2, fill)
            diameter = th - self.px(6)
            tx = x + w - diameter - self.px(5) if checked else x + self.px(5)
            rounded(hdc, tx, ty + self.px(3), diameter, diameter, diameter / 2, WHITE)
        elif cid in (ID_CLOSE, ID_MINIMIZE, ID_REFRESH, ID_LESS, ID_MORE):
            if cid in (ID_LESS, ID_MORE):
                u.FillRect(hdc, ctypes.byref(rc), self.brushes["input"])
            if hovered or pressed:
                rounded(hdc, x, y, w, h, self.px(7), rgb("FBEAEC") if cid == ID_CLOSE else TINT)
            col = (DANGER if cid == ID_CLOSE and hovered else INK) if enabled else DISABLED
            cx, cy, d = x + w / 2, y + h / 2, self.px(5)
            if cid == ID_CLOSE:
                line(hdc, cx - d, cy - d, cx + d, cy + d, col, self.px(1.4))
                line(hdc, cx + d, cy - d, cx - d, cy + d, col, self.px(1.4))
            elif cid == ID_REFRESH:
                text(hdc, "↻", (x, y - self.px(2), w, h), self.fonts["hero"], col, "center")
            else:
                line(hdc, cx - d, cy, cx + d, cy, col, self.px(1.4))
                if cid == ID_MORE:
                    line(hdc, cx, cy - d, cx, cy + d, col, self.px(1.4))
        else:
            primary = cid == ID_PLAY or (cid == ID_SAVE and self.dirty)
            preset = PRESET_IDS.get(cid)
            category = CATEGORY_IDS.get(cid)
            chosen = ((preset is not None and preset == burst_seconds(self.cfg.get("burst_seconds")))
                      or (category is not None and category == self.category))
            if not enabled:
                fill, edge, fg = (TINT, None, ACCENT) if cid == ID_LIBUSE and self.selected_item() else (INPUT, BORDER, DISABLED)
            elif primary:
                fill, edge, fg = ACCENT_DOWN if pressed else (ACCENT_HOVER if hovered else ACCENT), None, WHITE
            else:
                fill, edge, fg = (TINT if hovered or chosen else CARD), (ACCENT if chosen or hovered else BORDER), (ACCENT if chosen else INK)
            rounded(hdc, x, y, w, h, self.px(8), fill, edge)
            text(hdc, get_text(hwnd), (x, y - self.px(1), w, h), self.fonts["small" if preset or category is not None else "body"], fg, "center")
        if enabled and (dis.itemState & 0x10) and not (dis.itemState & 0x200):
            inset = self.px(3)
            focus_rect = wt.RECT(x + inset, y + inset, x + w - inset, y + h - inset)
            u.DrawFocusRect(hdc, ctypes.byref(focus_rect))

    def _draw_list_item(self, dis):
        if dis.itemID == 0xFFFFFFFF or dis.itemID >= len(self.visible):
            return
        rc, hdc = dis.rcItem, dis.hDC
        u.FillRect(hdc, ctypes.byref(rc), self.brushes["card"])
        item = self.items[self.visible[dis.itemID]]
        selected, current = bool(dis.itemState & 1), same_path(item[1], self.cfg.get("video", ""))
        x, y, w, h = rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top
        if selected:
            rounded(hdc, x + self.px(2), y + 1, w - self.px(4), h - 2, self.px(7), TINT)
            rounded(hdc, x + self.px(5), y + self.px(9), self.px(3), h - self.px(18), self.px(2), ACCENT)
        text(hdc, item[0], (x + self.px(16), y, w - self.px(152), h), self.fonts["body"], ACCENT if selected else INK)
        status = "使用中" if current else os.path.splitext(item[1])[1][1:].upper()
        suffix = CATEGORY_LABELS[classify_video(item[1])] + " · " + status
        text(hdc, suffix, (x + w - self.px(128), y, self.px(112), h), self.fonts["small"], ACCENT if current else MUTED, "right")
        if dis.itemState & 0x10:
            focus = wt.RECT(x + self.px(3), y + 2, x + w - self.px(3), y + h - 2)
            u.DrawFocusRect(hdc, ctypes.byref(focus))

    def _button_wndproc(self, hwnd, msg, wp, lp):
        try:
            if hwnd in self.checks:
                if msg == BM_GETCHECK:
                    return int(self.checks[hwnd])
                if msg == BM_SETCHECK:
                    self.checks[hwnd] = bool(wp)
                    u.InvalidateRect(hwnd, None, False)
                    return 0
            # Let BUTTON handle capture, drag cancellation, Space and BM_CLICK.
            # Its BN_CLICKED notification is the single place a toggle changes.
            if msg == WM_MOUSEMOVE and self.hover != hwnd:
                previous, self.hover = self.hover, hwnd
                if previous and u.IsWindow(previous):
                    u.InvalidateRect(previous, None, False)
                event = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 2, hwnd, 0)
                u.TrackMouseEvent(ctypes.byref(event))
                u.InvalidateRect(hwnd, None, False)
            elif msg == WM_MOUSELEAVE and self.hover == hwnd:
                self.hover = None
                u.InvalidateRect(hwnd, None, False)
            old = self.old_procs.get(hwnd)
            return u.CallWindowProcW(old, hwnd, msg, wp, lp) if old else u.DefWindowProcW(hwnd, msg, wp, lp)
        except Exception as exc:
            self.errors.append(str(exc))
            traceback.print_exc()
            return u.DefWindowProcW(hwnd, msg, wp, lp)

    def _command(self, cid, code):
        if self.initializing:
            return
        if cid == ID_SEARCH and code == 0x300:  # EN_CHANGE
            item = self.selected_item()
            self._rebuild_list(item[1] if item else self.cfg.get("video", ""))
        elif cid in CATEGORY_IDS and code == 0:
            item = self.selected_item()
            self.category = CATEGORY_IDS[cid]
            self._rebuild_list(item[1] if item else self.cfg.get("video", ""))
        elif cid == ID_BURST and code == 0x300 and not self.setting_text:
            self.dirty = get_text(self.controls["burst"]) != str(burst_seconds(self.cfg.get("burst_seconds")))
            self._update_controls()
        elif cid == ID_LIBLIST and code in (1, 2):
            self.selection = u.SendMessageW(self.controls["library"], LB_GETCURSEL, 0, 0)
            self._preview_changed()
            if code == 2:
                self.apply_selection()
        elif code != 0:
            return
        elif cid == ID_CLOSE:
            u.DestroyWindow(self.hwnd)
        elif cid == ID_MINIMIZE:
            u.ShowWindow(self.hwnd, 6)
        elif cid == ID_PLAY:
            self._player_action("play")
        elif cid == ID_STOP:
            self._player_action("stop" if self.services.is_running() else "start")
        elif cid == ID_SAVE:
            self.save_duration()
        elif cid in PRESET_IDS:
            self.save_duration(PRESET_IDS[cid])
        elif cid in (ID_LESS, ID_MORE) and not self.always:
            value = burst_seconds(get_text(self.controls["burst"])) + (1 if cid == ID_MORE else -1)
            self._duration_text(max(3, min(30, value)))
        elif cid == ID_ALWAYS:
            if "player" in self.jobs:
                return
            want = not self.always
            self._commit(always_play=want, nudge=time.time())
            self.always = want
            self.flash("持续播放已开启 · 不可见时仍会自动暂停" if want else "已恢复按需播放 · 播放几秒后自动定格")
            if want and not self.services.is_running():
                self._player_action("start")
        elif cid == ID_AUTO:
            self._toggle_auto()
        elif cid == ID_INSTALL:
            self._install_screensaver()
        elif cid == ID_LIBUSE:
            self.apply_selection()
        elif cid == ID_REFRESH:
            self.rescan()
        elif cid == ID_BROWSE:
            self.browse_video()
        else:
            return
        if not self.closed:
            self.refresh()

    def _paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = u.BeginPaint(hwnd, ctypes.byref(ps))
        memory, bitmap, old = None, None, None
        try:
            memory = g.CreateCompatibleDC(hdc)
            bitmap = g.CreateCompatibleBitmap(hdc, self.width, self.height)
            if memory and bitmap:
                old = g.SelectObject(memory, bitmap)
                self.draw(memory)
                g.BitBlt(hdc, 0, 0, self.width, self.height, memory, 0, 0, 0xCC0020)
            else:
                self.draw(hdc)
        finally:
            if old:
                g.SelectObject(memory, old)
            if bitmap:
                g.DeleteObject(bitmap)
            if memory:
                g.DeleteDC(memory)
            u.EndPaint(hwnd, ctypes.byref(ps))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            return self._handle_message(hwnd, msg, wp, lp)
        except Exception as exc:
            if not isinstance(exc, (ConfigError, OSError, ValueError)):
                self.errors.append(str(exc))
                traceback.print_exc()
            if self.controls and not self.closed:
                self.flash("操作未保存：" + str(exc), True, 8)
            return 0

    def _handle_message(self, hwnd, msg, wp, lp):
        if msg == WM_COMMAND:
            self._command(wp & 0xFFFF, (wp >> 16) & 0xFFFF)
            return 0
        if msg == WM_APP_RESULT:
            self._drain_results()
            return 0
        if msg == WM_TIMER:
            self._drain_results()
            return 0
        if msg == WM_PAINT and self.brushes:
            self._paint(hwnd)
            return 0
        if msg == WM_ERASEBKGND:
            return 1  # Double-buffered WM_PAINT owns the background.
        if msg == WM_PRINTCLIENT and self.brushes:
            self.draw(wt.HDC(wp))
            return 0
        if msg == WM_NCHITTEST:
            pt = wt.POINT(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
            u.ScreenToClient(hwnd, ctypes.byref(pt))
            return 2 if 0 <= pt.y < self.px(TITLE_HEIGHT) else 1
        if msg == WM_DPICHANGED and self.controls:
            suggested = ctypes.cast(lp, ctypes.POINTER(wt.RECT)).contents
            self._layout((wp & 0xFFFF) / 96.0, suggested)
            return 0
        if msg == WM_MEASUREITEM:
            measure = ctypes.cast(lp, ctypes.POINTER(MEASUREITEMSTRUCT)).contents
            if measure.CtlType == 2:
                measure.itemHeight = self.px(32)
                return 1
        if msg == WM_DRAWITEM:
            dis = ctypes.cast(lp, ctypes.POINTER(DRAWITEMSTRUCT)).contents
            if dis.CtlType == 4:
                self._draw_button(dis)
                return 1
            if dis.CtlType == 2:
                self._draw_list_item(dis)
                return 1
        if msg in (WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC):
            hdc = wt.HDC(wp)
            g.SetBkMode(hdc, 2)
            g.SetBkColor(hdc, INPUT)
            g.SetTextColor(hdc, DISABLED if self.always and lp == self.controls.get("burst") else INK)
            return self.brushes.get("input", 0)
        if msg == WM_CTLCOLORLISTBOX:
            g.SetBkColor(wt.HDC(wp), CARD)
            return self.brushes.get("card", 0)
        if msg == WM_CLOSE:
            u.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.closed = True
            u.KillTimer(hwnd, 1)
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    def _keyboard(self, msg):
        if msg.message != WM_KEYDOWN:
            return False
        focus = u.GetFocus()
        if msg.wParam == ord("F") and u.GetKeyState(0x11) & 0x8000:
            u.SetFocus(self.controls["search"])
            u.SendMessageW(self.controls["search"], 0xB1, 0, -1)
            return True
        if msg.wParam == 0x1B and focus == self.controls["search"] and get_text(focus):
            self.set_text("search", "")
            return True
        if msg.wParam == 0xD:
            if focus == self.controls["burst"]:
                self.save_duration()
                return True
            if focus == self.controls["library"]:
                self.apply_selection()
                return True
            if focus == self.controls["search"]:
                if self.visible:
                    u.SetFocus(self.controls["library"])
                return True
            if focus in self.old_procs and u.IsWindowEnabled(focus):
                u.SendMessageW(focus, BM_CLICK, 0, 0)
                return True
        return False

    def run(self):
        existing = u.FindWindowW(self.window_class, None)
        if existing:
            u.ShowWindow(existing, 9)
            u.SetForegroundWindow(existing)
            if self.ready:
                self.ready.set()
            return
        try:
            _api(u, "SetProcessDpiAwarenessContext", wt.BOOL, wt.HANDLE)(ctypes.c_void_p(-4))
        except AttributeError:
            pass
        dc = u.GetDC(None)
        try:
            dpi_scale = (g.GetDeviceCaps(dc, 88) or 96) / 96.0
        finally:
            u.ReleaseDC(None, dc)
        self.hinst = k.GetModuleHandleW(None)
        self.brushes = {"bg": g.CreateSolidBrush(BG), "card": g.CreateSolidBrush(CARD), "input": g.CreateSolidBrush(INPUT)}
        self._read_config()
        self.always, self.status = bool(self.cfg.get("always_play")), {}
        x, y = self._layout(dpi_scale)
        icon_path = os.path.join(self.services.APP_DIR, "assets", "icon.ico")
        if os.path.isfile(icon_path):
            self.icon = u.LoadImageW(None, icon_path, 1, 32, 32, 0x10)
        wc = WNDCLASS()
        wc.style, wc.lpfnWndProc, wc.hInstance = 0x8, self._proc, self.hinst
        wc.hIcon, wc.hCursor = self.icon, u.LoadCursorW(None, 32512)
        wc.lpszClassName = self.window_class
        if not u.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.hwnd = u.CreateWindowExW(0, self.window_class, "Aerial · 壁纸偏好设置",
                                         WS_POPUP | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN,
                                         x, y, self.width, self.height, None, None, self.hinst, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._create_controls()
            self.initializing = False
            try:
                dpi = _api(u, "GetDpiForWindow", wt.UINT, H)(self.hwnd)
                dpi_scale = (dpi or 96) / 96.0
            except AttributeError:
                pass
            self._layout(dpi_scale)
            self._shadow()
            self.refresh()
            self.start_job("auto_check", self.services.task_installed)
            self.rescan()
            u.SetTimer(self.hwnd, 1, 1000, None)
            u.ShowWindow(self.hwnd, 1)
            # Never force a preferences window permanently above other apps.
            u.SetForegroundWindow(self.hwnd)
            if self.ready:
                self.ready.set()
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                try:
                    handled = self._keyboard(msg)
                except Exception as exc:
                    self.flash("操作未保存：" + str(exc), True)
                    handled = True
                if not handled and not u.IsDialogMessageW(self.hwnd, ctypes.byref(msg)):
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
        finally:
            self.closed = True
            if self.hwnd and u.IsWindow(self.hwnd):
                u.DestroyWindow(self.hwnd)
            self._dispose_image()
            for font in self.fonts.values():
                g.DeleteObject(font)
            for brush in self.brushes.values():
                g.DeleteObject(brush)
            u.UnregisterClassW(self.window_class, self.hinst)
            if self.icon:
                u.DestroyIcon(self.icon)

    def _shadow(self):
        try:
            class Margins(ctypes.Structure):
                _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                            ("top", ctypes.c_int), ("bottom", ctypes.c_int)]
            dwm = ctypes.WinDLL("dwmapi")
            extend = _api(dwm, "DwmExtendFrameIntoClientArea", ctypes.c_long, H, ctypes.POINTER(Margins))
            extend(self.hwnd, ctypes.byref(Margins(1, 1, 1, 1)))
        except (OSError, AttributeError):
            pass


def run_ui(services, window_class="AerialWallpaperUI", ready=None):
    panel = PreferencesPanel(services, window_class, ready)
    panel.run()
    return panel
