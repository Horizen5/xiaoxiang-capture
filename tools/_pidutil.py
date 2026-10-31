# -*- coding: utf-8 -*-
"""pid 文件的小工具：写的时候带上「谁 + 什么时候起的」，读的时候能防 pid 复用。

背景：Windows 会复用 pid。上一轮 start_beta 退出后，它占过的 pid 可能被一个
node 调试代理捡走，于是下次双击 .bat 会因为「pid 还活着」直接退出。
光看 pid 活着是不够的，必须连进程身份一起校验。
"""
import ctypes
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def proc_info(pid):
    """返回 (可执行文件名小写, 创建时间 FILETIME)；拿不到就是 ('', 0)。"""
    if not pid:
        return ('', 0)
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))       # QUERY_LIMITED_INFORMATION
    if not h:
        return ('', 0)
    try:
        exe = ''
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            exe = buf.value.replace('/', '\\').rsplit('\\', 1)[-1].lower()
        c, e, k, u = (ctypes.c_ulonglong() for _ in range(4))
        ctime = 0
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                               ctypes.byref(k), ctypes.byref(u)):
            ctime = c.value
        return (exe, ctime)
    except Exception:
        return ('', 0)
    finally:
        k32.CloseHandle(h)


def write_pid(name, pid=None, base=None):
    pid = pid or os.getpid()
    exe, ctime = proc_info(pid)
    try:
        with open(os.path.join(base or BASE, name), 'w', encoding='utf-8') as f:
            f.write('%d:%s:%d' % (pid, exe, ctime))
    except Exception:
        pass


def read_pid(name, base=None):
    """返回 (pid, exe, ctime)。老格式（只有 pid）会得到 exe=''、ctime=0。"""
    try:
        with open(os.path.join(base or BASE, name), encoding='utf-8') as f:
            raw = f.read().strip()
    except Exception:
        return (0, '', 0)
    parts = raw.replace('|', ':').split(':') if raw else []
    try:
        pid = int(parts[0])
    except Exception:
        return (0, '', 0)
    exe = parts[1].lower() if len(parts) > 1 else ''
    try:
        ctime = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        ctime = 0
    return (pid, exe, ctime)


def pid_matches(name, pid, exe, ctime, base=None):
    """这个 pid 现在还是当初那个进程吗？"""
    if not pid:
        return False
    cur_exe, cur_ct = proc_info(pid)
    if not cur_exe:                     # 进程已经不在了
        return False
    if exe and cur_exe != exe:
        return False
    if ctime and cur_ct and cur_ct != ctime:
        return False
    if not exe and not ctime:           # 老格式只能靠「是自己人」兜底
        return cur_exe in ('python.exe', 'pythonw.exe')
    return True
