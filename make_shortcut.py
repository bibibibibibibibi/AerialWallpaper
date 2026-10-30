# -*- coding: utf-8 -*-
"""用 ctypes 直接创建 Windows 快捷方式（.lnk），不依赖 COM 自动化库。

用法:
  python make_shortcut.py <lnk路径> <目标exe> [工作目录] [图标路径]
"""
import ctypes
from ctypes import byref
import sys
import os

OLE32 = ctypes.OleDLL('ole32')
SHELL32 = ctypes.OleDLL('shell32')


def guid(s):
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                    ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]
    s = s.strip('{}')
    parts = s.split('-')
    g = GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    for i in range(2):  # 第四段 4 位 hex = 2 字节，填 Data4[0:2]
        g.Data4[i] = int(parts[3][i * 2:i * 2 + 2], 16)
    for i in range(6):  # 最后一段 12 位 hex = 6 字节，填 Data4[2:8]
        g.Data4[2 + i] = int(parts[4][i * 2:i * 2 + 2], 16)
    return g


CLSID_ShellLink = guid('00021401-0000-0000-C000-000000000046')
IID_IShellLinkW = guid('000214F9-0000-0000-C000-000000000046')
IID_IPersistFile = guid('0000010B-0000-0000-C000-000000000046')
CLSCTX_INPROC_SERVER = 1

STGM_READ = 0


def make_lnk(lnk_path, target, workdir=None, icon=None, desc=''):
    OLE32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    ppv = ctypes.c_void_p()
    OLE32.CoCreateInstance(byref(CLSID_ShellLink), None, CLSCTX_INPROC_SERVER,
                             byref(IID_IShellLinkW), byref(ppv))
    # IShellLinkW vtable: QI(0) AddRef(1) Release(2) GetPath(3) ... SetPath(20)
    vtbl = ctypes.cast(ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p)).contents,
                       ctypes.POINTER(ctypes.c_void_p))
    SetPath = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[20])
    SetWorkingDirectory = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[9])
    SetDescription = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[7])
    SetIconLocation = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)(vtbl[17])
    r1 = SetPath(ppv, target)
    r2 = SetWorkingDirectory(ppv, workdir or os.path.dirname(target))
    if desc:
        SetDescription(ppv, desc)
    if icon:
        SetIconLocation(ppv, icon, 0)
    # QueryInterface -> IPersistFile
    pf = ctypes.c_void_p()
    QueryInterface = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(vtbl[0])
    QueryInterface(ppv, byref(IID_IPersistFile), byref(pf))
    pvtbl = ctypes.cast(ctypes.cast(pf, ctypes.POINTER(ctypes.c_void_p)).contents,
                        ctypes.POINTER(ctypes.c_void_p))
    Save = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)(pvtbl[6])
    r3 = Save(pf, lnk_path, 1 if False else 0)  # fRemember=FALSE
    for release in (pf, ppv):
        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
            ctypes.cast(ctypes.cast(release, ctypes.POINTER(ctypes.c_void_p)).contents,
                        ctypes.POINTER(ctypes.c_void_p))[2])
        Release(release)
    if r1 or r2 or r3:
        raise OSError(f'IShellLink failed: {r1},{r2},{r3}')
    print('已创建: ' + lnk_path)


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    lnk = os.path.abspath(args[0])
    make_lnk(lnk, os.path.abspath(args[1]),
             os.path.abspath(args[2]) if len(args) > 2 and args[2] else None,
             os.path.abspath(args[3]) if len(args) > 3 and args[3] else None)
    print('存在: ' + str(os.path.exists(lnk)))
