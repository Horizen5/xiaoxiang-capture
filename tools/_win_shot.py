# -*- coding: utf-8 -*-
"""按窗口标题截图，方便验证 UI 自动化到底点没点对。
用法：python _win_shot.py 微信 out.png
      python _win_shot.py --list
"""
import ctypes
import sys
from ctypes import wintypes

u = ctypes.WinDLL('user32', use_last_error=True)
g = ctypes.WinDLL('gdi32', use_last_error=True)


def enum_all():
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out = []

    def cb(hwnd, lp):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(200)
        u.GetClassNameW(hwnd, cls, 200)
        out.append((hwnd, buf.value, cls.value))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def find(key, exact=False):
    for hwnd, title, cls in enum_all():
        if (title == key) if exact else (key in title):
            return hwnd, title
    return 0, ''


def shot(hwnd, path):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return False, '窗口尺寸 0'
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    # 微信是 Qt + GPU 渲染，BitBlt 抓 WindowDC 会得到全黑；
    # PrintWindow + PW_RENDERFULLCONTENT(2) 才能拿到真实画面（窗口被遮挡也行）
    ok = u.PrintWindow(hwnd, mdc, 2)
    if not ok:
        ok = g.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
    if not ok:
        return False, '抓图失败'
    # 手工拼 BMP 文件（不依赖 Pillow）
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h      # 负数 = top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    stride = w * 4
    buf = ctypes.create_string_buffer(stride * h)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    import struct
    hdr = struct.pack('<2sIHHI', b'BM', 14 + 40 + len(buf), 0, 0, 14 + 40)
    inf = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, len(buf), 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr + inf + buf.raw)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    return True, path


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--list':
        for hwnd, title, cls in enum_all():
            print(hwnd, '|', title, '|', cls)
        sys.exit()
    key = sys.argv[1] if len(sys.argv) > 1 else '微信'
    out = sys.argv[2] if len(sys.argv) > 2 else '_shot.png'
    if key.isdigit():                 # 也支持直接给窗口句柄
        hwnd, title = int(key), '(hwnd)'
    else:
        hwnd, title = find(key)
    if not hwnd:
        print('没找到窗口:', key)
        sys.exit(1)
    ok, msg = shot(hwnd, out)
    print(hwnd, title, ok, msg)
