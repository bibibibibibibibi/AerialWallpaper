# -*- coding: utf-8 -*-
"""启动源码面板并截全窗口图（PrintWindow + DPI aware）"""
import ctypes, os, subprocess, sys, time
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
u = ctypes.WinDLL("user32")
g = ctypes.WinDLL("gdi32")
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.FindWindowW.restype = wintypes.HWND
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
g.CreateCompatibleDC.argtypes = [wintypes.HDC]
g.CreateCompatibleDC.restype = wintypes.HDC
g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
g.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

subprocess.Popen([sys.executable, "app.py"], cwd=BASE,
                 creationflags=0x00000008 | 0x08000000, close_fds=True)
hwnd = None
for _ in range(30):
    time.sleep(0.5)
    hwnd = u.FindWindowW("AerialWallpaperUI", None)
    if hwnd:
        # 竞态：可能抓到正在销毁的旧窗口（尺寸异常小），等尺寸稳定
        rc0 = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rc0))
        if (rc0.right - rc0.left) > 800:
            break
        hwnd = None
if not hwnd:
    sys.exit("面板未启动")
time.sleep(2)

rc = wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(rc))
w, h = rc.right - rc.left, rc.bottom - rc.top
print("window:", w, h)

hdc = g.CreateCompatibleDC(None)
bmp = g.CreateCompatibleBitmap(u.GetDC(None), w, h)
g.SelectObject(hdc, bmp)
u.PrintWindow(hwnd, hdc, 2)  # PW_RENDERFULLCONTENT

class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p)]
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]
class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

bm = BITMAP()
g.GetObjectW(bmp, ctypes.sizeof(bm), ctypes.byref(bm))
bi = BITMAPINFO()
bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.bmiHeader.biWidth = bm.bmWidth
bi.bmiHeader.biHeight = -bm.bmHeight
bi.bmiHeader.biPlanes = 1
bi.bmiHeader.biBitCount = 32
size = bm.bmWidth * bm.bmHeight * 4
buf = ctypes.create_string_buffer(size)
g.GetDIBits(hdc, bmp, 0, bm.bmHeight, buf, ctypes.byref(bi), 0)

from PIL import Image
img = Image.frombuffer("RGBA", (bm.bmWidth, bm.bmHeight), buf, "raw", "BGRA", 0, 1)
out = os.path.join(BASE, "_new_ui.png")
img.convert("RGB").save(out)
print("saved:", out)
