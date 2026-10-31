# -*- coding: utf-8 -*-
"""在微信 PC 版里用「搜索框 → 输入关键词 → 回车」的方式打开小程序。

PC 微信没有可用的 scheme 唤起小程序（weixin://dl/business 需要服务端短链 token），
但它的搜索面板里「最近使用过的小程序」是现成的入口，所以这里直接操作 UI。

设计原则：
  · 只在微信窗口真的在前台时才敲键盘，绝不把字打到别人的对话框里
  · 每一步都可截图核对（_ui_step*.bmp）
  · 打开成功以「出现标题含关键词的小程序窗口」为准，不靠猜

用法：
  python _open_mini.py xxcs 小象超市          # 打开
  python _open_mini.py --probe                # 只看看现在什么状态
"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.WinDLL('user32', use_last_error=True)
k32 = ctypes.windll.kernel32
BASE = __import__('os').path.dirname(__import__('os').path.abspath(__file__))

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


# ---------------- 键盘 ----------------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', _U)]


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL, VK_RETURN, VK_ESCAPE, VK_F, VK_DOWN = 0x11, 0x0D, 0x1B, 0x46, 0x28


def _send(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return u.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))


def _key(vk, up=False):
    return INPUT(type=1, ki=KEYBDINPUT(wVk=vk, wScan=0,
                                       dwFlags=KEYEVENTF_KEYUP if up else 0,
                                       time=0, dwExtraInfo=0))


def _uni(ch, up=False):
    return INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=ord(ch),
                                       dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                                       time=0, dwExtraInfo=0))


def send_key(vk):
    _send(_key(vk), _key(vk, True))


def send_ctrl(vk):
    _send(_key(VK_CONTROL), _key(vk), _key(vk, True), _key(VK_CONTROL, True))


def type_text(s):
    for ch in s:
        _send(_uni(ch), _uni(ch, True))
        time.sleep(0.03)


# ---------------- 窗口 ----------------
def _proc_name(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = wintypes.DWORD(512)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.rsplit('\\', 1)[-1].lower()
        return ''
    finally:
        k32.CloseHandle(h)


def windows(proc_filter=None, visible_only=False, min_size=(60, 60)):
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out = []

    def cb(hwnd, lp):
        if visible_only and not u.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pn = _proc_name(pid.value)
        if proc_filter and not any(k in pn for k in proc_filter):
            return True
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < min_size[0] or h < min_size[1]:
            return True
        cls = ctypes.create_unicode_buffer(200)
        u.GetClassNameW(hwnd, cls, 200)
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        out.append({'hwnd': hwnd, 'title': buf.value, 'cls': cls.value,
                    'proc': pn, 'vis': bool(u.IsWindowVisible(hwnd)),
                    'rect': (r.left, r.top, w, h)})
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def wechat_main():
    """微信主窗口：Weixin.exe 的可见 Qt 窗口，标题「微信」。"""
    for w in windows(('weixin.exe',), visible_only=True, min_size=(400, 400)):
        if w['title'] == '微信' or w['title'].startswith('微信'):
            if w['cls'].startswith('Qt'):
                return w
    for w in windows(('weixin.exe',), visible_only=True, min_size=(400, 400)):
        if w['cls'].startswith('Qt'):
            return w
    return None


def mini_window(keyword):
    """小程序窗口：WeChatAppEx 的 Chrome_WidgetWin_0，标题含关键词。"""
    for w in windows(('wechatappex',), visible_only=True):
        if w['cls'].startswith('Chrome_WidgetWin') and keyword in (w['title'] or ''):
            return w
    return None


def focus(hwnd):
    """后台进程想抢前台必须借前台线程的输入队列，否则 SetForegroundWindow 直接失败。"""
    fg = u.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur = k32.GetCurrentThreadId()
    tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_t = u.GetWindowThreadProcessId(hwnd, None)
    attached = []
    for tid in (tid_fg, tid_t):
        if tid and tid != cur:
            if u.AttachThreadInput(cur, tid, True):
                attached.append(tid)
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetFocus(hwnd)
    finally:
        for tid in attached:
            u.AttachThreadInput(cur, tid, False)
    time.sleep(0.25)
    return u.GetForegroundWindow() == hwnd


def shot(hwnd, path):
    import subprocess
    return subprocess.run([sys.executable, '_win_shot.py', str(hwnd), path],
                          cwd=BASE, capture_output=True).returncode == 0


# ---------------- 主流程 ----------------
def open_mini(keyword, app_name, tries=2, verbose=True, dry=False):
    def log(*a):
        if verbose:
            print(*a, flush=True)

    mw = wechat_main()
    if not mw:
        return {'ok': False, 'msg': '没找到微信主窗口，先把微信打开'}
    log('微信主窗口 %s %dx%d' % (mw['hwnd'], mw['rect'][2], mw['rect'][3]))

    for i in range(tries):
        if mini_window(app_name):
            return {'ok': True, 'msg': '小程序已经开着', 'how': 'already'}
        if not focus(mw['hwnd']):
            return {'ok': False, 'msg': '没法把微信调到前台（可能被全屏程序挡住）'}
        time.sleep(0.3)
        send_ctrl(VK_F)              # 微信搜索快捷键
        time.sleep(0.55)
        type_text(keyword)
        time.sleep(0.9)
        _shot(mw['hwnd'], '_ui_step1.bmp')
        if dry:
            log('只输入不回车，看 _ui_step1.bmp')
            return {'ok': True, 'msg': 'dry run', 'how': 'dry'}

        # 回车打开搜索结果的默认项（微信会优先给「最近使用过的小程序」）
        send_key(VK_RETURN)
        for _ in range(16):          # 最多等 8 秒
            time.sleep(0.5)
            if mini_window(app_name):
                return {'ok': True, 'msg': '已打开 %s' % app_name, 'how': 'enter'}
        _shot(mw['hwnd'], '_ui_step2.bmp')
        log('第 %d 次回车没打开，清掉搜索框重试' % (i + 1))
        send_key(VK_ESCAPE)
        time.sleep(0.4)

    return {'ok': False, 'msg': '搜索到了但没能进入小程序，看 _ui_step2.bmp'}


def _shot(hwnd, name):
    import os
    p = os.path.join(BASE, name)
    shot(hwnd, p)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        mw = wechat_main()
        print('主窗口:', mw)
        for w in windows(('wechatappex',)):
            print('  AppEx:', w['hwnd'], w['vis'], w['cls'], repr(w['title']), w['rect'])
        sys.exit()
    kw = sys.argv[1] if len(sys.argv) > 1 else 'xxcs'
    app = sys.argv[2] if len(sys.argv) > 2 else '小象超市'
    print(open_mini(kw, app, dry='--dry' in sys.argv))
