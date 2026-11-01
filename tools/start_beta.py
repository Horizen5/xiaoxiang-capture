# -*- coding: utf-8 -*-
"""小象超市 · 手动抓包 beta 一键启动

干的事：
  1. 检查小程序进程（WeChatAppEx）在不在
  2. 起调试代理（wmpfdebugger，端口 62000），并等小程序真的连上来
  3. 起看板（端口 8899）
  4. 起抓包记账（live_capture.py）
  5. 常驻守护：任何一个进程挂了自动拉起来；调试代理重启后抓包会自己接回去

用法：
  python -u start_beta.py            正常启动（手动翻页，脚本不自己发包）
  python -u start_beta.py --cruise   让脚本空闲时自己补页（有风控风险）
  python -u start_beta.py --no-open  不自动打开浏览器

退出：在这个窗口按 Ctrl+C，子进程会一起关掉。
"""
import asyncio
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

import websockets

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
RAW = os.path.join(BASE, 'live_raw')
PROXY_DIR = os.path.join(BASE, 'wmpfdebugger')
PROXY_LOG = os.path.join(PROXY_DIR, 'wmpf_now.log')
CAP_LOG = os.path.join(BASE, 'live_stdout.log')
BOARD_LOG = os.path.join(BASE, 'monitor.log')

APP_HINT = 'wx92916b3adca84096'
PROXY_PORT = 62000
BOARD_PORT = 8899
BOARD_URL = 'http://127.0.0.1:%d/' % BOARD_PORT

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
procs = {'proxy': None, 'board': None, 'capture': None}
STATE = {'no_node': False, 'proxy_hint_at': 0.0}


# ---------------- 基础探测 ----------------
def say(msg, mark='  '):
    print('%s %s' % (mark, msg), flush=True)


def port_open(port, tmo=1.0):
    try:
        s = socket.socket()
        s.settimeout(tmo)
        s.connect(('127.0.0.1', port))
        s.close()
        return True
    except Exception:
        return False


def http_json(url, tmo=2.0):
    try:
        with urllib.request.urlopen(url, timeout=tmo) as r:
            return json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception:
        return None


def proxy_up():
    # 62000 只接 WebSocket，普通 HTTP 会回 426，所以只测端口
    return port_open(PROXY_PORT)


_targets_cache = {'t': 0.0, 'n': 0}


def mini_targets(force=False):
    """调试代理里挂着的、属于这个小程序的 target 数量。>0 说明抓包能接上。

    62000 没有 HTTP 接口，只能开一条 CDP 连接问 Target.getTargets。
    结果缓存 2 秒，避免频繁开关连接。
    """
    now = time.time()
    if not force and now - _targets_cache['t'] < 2.0:
        return _targets_cache['n']
    _targets_cache['t'] = now

    async def _probe():
        ws = await websockets.connect('ws://127.0.0.1:%d' % PROXY_PORT,
                                      max_size=None, open_timeout=2.5)
        try:
            await ws.send(json.dumps({'id': 1, 'method': 'Target.getTargets'}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.5)
                m = json.loads(raw)
                if m.get('id') == 1:
                    return (m.get('result') or {}).get('targetInfos') or []
        finally:
            await ws.close()

    try:
        infos = asyncio.run(_probe())
    except Exception:
        infos = []
    n = 0
    for t in infos:
        if APP_HINT in (t.get('url') or ''):
            n += 1
    _targets_cache['n'] = n
    return n


def board_up():
    return http_json('http://127.0.0.1:%d/api/cur' % BOARD_PORT, tmo=1.5) is not None


def wechat_pids():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq WeChatAppEx.exe',
                              '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, encoding='gbk',
                             errors='ignore', creationflags=CREATE_NO_WINDOW).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) > 1:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def pages():
    try:
        return len([f for f in os.listdir(RAW) if f.startswith('c') and '_o' in f])
    except Exception:
        return 0


def current():
    try:
        with open(os.path.join(RAW, '_current.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def tail(path, n=1):
    try:
        with open(path, 'rb') as f:
            data = f.read()[-8000:].decode('utf-8', 'ignore')
        return [l for l in data.splitlines() if l.strip()][-n:]
    except Exception:
        return []


# ---------------- 启动子进程 ----------------
def _hidden_si():
    """再兜一层：即使进程偷开了控制台，也用 SW_HIDE 把它藏掉，不抢焦点。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0          # SW_HIDE
    return si


def spawn(name, args, cwd, log, detached=False):
    f = open(log, 'w', encoding='utf-8', errors='replace')
    # 注意：detached 原来用的是 DETACHED_PROCESS，那是「不继承父控制台」，
    # Windows 会给控制台程序新开一个可见黑窗（就是那个标题为「选择 …node.exe」的窗口）。
    # 改用 CREATE_NO_WINDOW：控制台存在但隐藏，同样是独立进程组，启动器退出后不受影响。
    flags = CREATE_NO_WINDOW
    if detached:
        flags |= CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(args, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags,
                         startupinfo=_hidden_si())
    p._logf = f
    procs[name] = p
    return p


def alive(name):
    p = procs.get(name)
    return bool(p and p.poll() is None)


def kill(name):
    p = procs.get(name)
    if p and p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    procs[name] = None


_node_cache = {'path': None, 'done': False}


def find_node():
    """双击 .bat 启动时 PATH 往往不含 node，这里自己按已知位置找一遍。"""
    if _node_cache['done']:
        return _node_cache['path']
    _node_cache['done'] = True

    cands = []

    # 1) WorkBuddy 自带的 node（和 Python 一样的目录结构）
    home = os.path.expanduser('~')
    base = os.path.join(home, '.workbuddy', 'binaries', 'node', 'versions')
    try:
        vers = sorted(os.listdir(base), reverse=True)   # 22.x 排在 20.x 前面
    except Exception:
        vers = []
    for v in vers:
        cands.append(os.path.join(base, v, 'node.exe'))
        cands.append(os.path.join(base, v, 'bin', 'node.exe'))

    # 2) 常规安装位置
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA', 'APPDATA',
                'ProgramData', 'Scoop'):
        root = os.environ.get(env)
        if not root:
            continue
        cands += [
            os.path.join(root, 'nodejs', 'node.exe'),
            os.path.join(root, 'Programs', 'nodejs', 'node.exe'),
            os.path.join(root, 'nvm', 'node.exe'),
            os.path.join(root, 'scoop', 'apps', 'nodejs', 'current', 'node.exe'),
            os.path.join(root, 'scoop', 'apps', 'nodejs-lts', 'current', 'node.exe'),
        ]

    # 3) nvm-windows 的 symlink 目录
    for root in (os.environ.get('NVM_SYMLINK'), r'C:\Program Files\nodejs',
                 r'C:\nvm4w\nodejs'):
        if root:
            cands.append(os.path.join(root, 'node.exe'))

    # 4) 最后才问 PATH
    w = shutil.which('node')
    if w:
        cands.append(w)

    for p in cands:
        if p and os.path.isfile(p):
            _node_cache['path'] = p
            return p
    return None


def start_proxy():
    node = find_node()
    tsx = os.path.join(PROXY_DIR, 'node_modules', 'tsx', 'dist', 'cli.mjs')
    if not node:
        if not STATE['no_node']:
            STATE['no_node'] = True
            print('-' * 62, flush=True)
            say('找不到 node.exe，调试代理起不来 → 抓包拿不到数据', '[!!]')
            say('已找过这些地方：', '[..]')
            say('  %s' % os.path.join(os.path.expanduser('~'), '.workbuddy',
                                      'binaries', 'node', 'versions', '*', 'node.exe'), '[..]')
            say('  C:\\Program Files\\nodejs\\node.exe', '[..]')
            say('装 Node.js 后重新双击 start beta.bat 即可；看板不受影响，仍可打开。', '[..]')
            print('-' * 62, flush=True)
        return False
    STATE['no_node'] = False
    say('启动调试代理 ... (node: %s)' % node)
    # 子进程（tsx 里可能再拉 node）需要 PATH 里有它
    ndir = os.path.dirname(node)
    if ndir and ndir.lower() not in os.environ.get('PATH', '').lower():
        os.environ['PATH'] = ndir + os.pathsep + os.environ.get('PATH', '')
    if not os.path.exists(tsx):
        say('找不到 tsx（%s），请先在 tools/wmpfdebugger 里 npm i' % tsx, '[!!]')
        return False
    spawn('proxy', [node, tsx, 'src\\index.ts', '--auto-detect'], PROXY_DIR, PROXY_LOG,
          detached=True)   # 独立进程：启动器关掉它也不死，下次启动不用重开小程序
    for _ in range(40):
        time.sleep(1.0)
        if proxy_up():
            say('调试代理已监听 %d' % PROXY_PORT, '[OK]')
            return True
        if not alive('proxy'):
            say('调试代理启动失败，日志末尾：%s' % (tail(PROXY_LOG) or '空'), '[!!]')
            return False
    say('调试代理 40 秒没起来，先继续（会在后台重试）', '[!!]')
    return False


def start_board():
    if board_up():
        say('看板已在运行', '[OK]')
        return True
    say('启动看板 ...')
    spawn('board', [sys.executable, '-u', 'monitor_server.py'], BASE, BOARD_LOG)
    for _ in range(20):
        time.sleep(0.7)
        if board_up():
            say('看板已就绪 %s' % BOARD_URL, '[OK]')
            return True
    say('看板没起来，日志：%s' % (tail(BOARD_LOG) or '空'), '[!!]')
    return False


def start_capture(cruise):
    env = os.environ.copy()
    env['CRUISE'] = '1' if cruise else '0'
    f = open(CAP_LOG, 'w', encoding='utf-8', errors='replace')
    p = subprocess.Popen([sys.executable, '-u', 'live_capture.py'], cwd=BASE,
                         stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=env, creationflags=CREATE_NO_WINDOW)
    p._logf = f
    procs['capture'] = p
    return p


# ---------------- 主流程 ----------------
def proc_info(pid):
    """拿 (可执行文件名小写, 进程创建时间)。

    pid 会被系统复用：10-30 那次「已经有一个 beta 在运行（pid 18172）」其实是
    上一轮 start_beta 的 pid 被一个 node 调试代理捡走了，光看 pid 活着必然误判。
    所以存 pid 时连「谁 + 什么时候起的」一起存，校验时三者对上才算同一个进程。
    """
    if not pid:
        return ('', 0)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ('', 0)
    try:
        exe = ''
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            exe = buf.value.replace('/', '\\').rsplit('\\', 1)[-1].lower()
        c, e, k, us = (ctypes.c_ulonglong() for _ in range(4))
        ctime = 0
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                               ctypes.byref(k), ctypes.byref(us)):
            ctime = c.value
        return (exe, ctime)
    except Exception:
        return ('', 0)
    finally:
        k32.CloseHandle(h)


def pid_alive(pid):
    """不用 PowerShell/WMI，直接问内核这个 pid 还活着吗。"""
    if not pid:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259      # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def kill_pid(pid):
    if not pid:
        return
    PROCESS_TERMINATE = 0x0001
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if h:
        try:
            k32.TerminateProcess(h, 1)
        finally:
            k32.CloseHandle(h)


def read_pid(name):
    """读 pid 文件，返回 (pid, exe, ctime)。旧格式只有 pid，exe 记为 '' 表示「不可信」。"""
    try:
        with open(os.path.join(BASE, name), encoding='utf-8') as f:
            raw = f.read().strip()
    except Exception:
        return (0, '', 0)
    if not raw:
        return (0, '', 0)
    parts = raw.replace('|', ':').split(':')
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


def write_pidfile(name, pid=None):
    """写 pid + 可执行文件名 + 创建时间，供下次启动校验。"""
    pid = pid or os.getpid()
    exe, ctime = proc_info(pid)
    try:
        with open(os.path.join(BASE, name), 'w', encoding='utf-8') as f:
            f.write('%d:%s:%d' % (pid, exe, ctime))
    except Exception:
        pass


def pid_matches(name, pid, exe, ctime):
    """这个 pid 现在还是当初那个进程吗？（防 pid 复用）"""
    if not pid or not pid_alive(pid):
        return False
    cur_exe, cur_ct = proc_info(pid)
    if exe and cur_exe and cur_exe != exe:
        return False
    if ctime and cur_ct and cur_ct != ctime:
        return False
    if not exe and not ctime:
        # 老格式：只能确认「像我们自己的 python」，别的一律不认，避免误杀
        return cur_exe in ('python.exe', 'pythonw.exe')
    return True


def clean_pidfile(name):
    try:
        os.remove(os.path.join(BASE, name))
    except Exception:
        pass


def another_supervisor():
    """有没有另一个 start_beta 已经在跑（靠 pid 文件，毫秒级判定）。

    只认「pid + 同一个 python.exe + 同一个创建时间」，pid 被复用不算。
    """
    pid, exe, ct = read_pid('_supervisor.pid')
    if pid and pid != os.getpid() and pid_matches('_supervisor.pid', pid, exe, ct):
        return [pid]
    if pid:
        # 留着不管会一直挡住下次启动，这里直接清掉（不杀进程，只清记录）
        cur_exe, _ = proc_info(pid)
        if cur_exe and cur_exe not in ('python.exe', 'pythonw.exe'):
            say('（清掉了一条过期的运行记录：pid %d 现在是 %s，不是上一轮的启动器）'
                % (pid, cur_exe), '[..]')
    clean_pidfile('_supervisor.pid')
    return []


def kill_stale():
    """收掉上一轮残留的抓包/看板进程（靠它们自己写的 pid 文件）。

    调试代理故意不动：它能在多次启动之间复用，小程序就不用每次重开。
    必须校验 pid 真的是我们那个 python，否则 pid 复用会杀掉无辜进程。
    """
    for name in ('_capture.pid', '_board.pid'):
        pid, exe, ct = read_pid(name)
        if pid and pid_matches(name, pid, exe, ct):
            kill_pid(pid)
        clean_pidfile(name)


def wait_miniapp(timeout=None):
    """等小程序连上调试代理。返回 True 表示连上了。"""
    t0 = time.time()
    last_hint = 0
    while True:
        if not proxy_up():
            return False
        n = mini_targets()
        if n > 0:
            return True
        if not alive('proxy'):
            return False
        if time.time() - last_hint > 12:
            last_hint = time.time()
            say('代理在等小程序，但小程序还没连上来 —— 请把它整个关掉再打开一次'
                '（只退到上一页往往不够，连接是启动时建立的）', '[..]')
        if timeout and time.time() - t0 > timeout:
            return False
        time.sleep(1.5)


def detach_self():
    """把自己交给系统，脱离当前 shell 会话。

    在交互式会话里直接跑 start_beta，父进程一退（或被中断）整棵进程树会被一起收走，
    抓包和看板跟着没。这里用 DETACHED_PROCESS 重新拉起自己，本进程立刻退出，
    之后服务归系统管，不受调用方影响。
    """
    DETACHED = 0x00000008
    NEW_GROUP = 0x00000200
    NO_WINDOW = 0x08000000
    args = [a for a in sys.argv[1:] if a != '--daemon']
    subprocess.Popen([sys.executable, os.path.abspath(__file__)] + args,
                     cwd=BASE,
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=DETACHED | NEW_GROUP | NO_WINDOW,
                     close_fds=True)
    print('已在后台启动，稍候会自动打开看板网页')
    print('  看板 http://127.0.0.1:8899/')
    sys.stdout.flush()
    os._exit(0)


def main():
    args = sys.argv[1:]
    # 默认纯手动翻页（不自己发包）。要脚本空闲时自动补页，加 --cruise。
    # 10-30 出过一次滑块验证，怀疑与重放发包有关，所以默认关。
    cruise = '--cruise' in args
    open_browser = '--no-open' not in args

    if '--daemon' in args:
        detach_self()

    if sys.platform == 'win32':
        try:
            os.system('chcp 65001 >nul')
        except Exception:
            pass

    print('=' * 62)
    print('  小象超市 · 抓包 beta    （%s）' % ('自动补页' if cruise else '手动翻页'))
    print('  抓包与看板全部后台运行，不弹窗、不抢焦点')
    print('=' * 62)

    others = another_supervisor()
    if others:
        # 已经有一份在跑：看板活着就直接把网页打开，别让用户自己找地址。
        if board_up():
            say('已经有一个 beta 在运行（pid %s）。' % ', '.join(map(str, others)), '[OK]')
            if open_browser:
                try:
                    webbrowser.open(BOARD_URL)
                    say('已启动网页：%s' % BOARD_URL, '[OK]')
                except Exception as e:
                    say('网页没打开，手动访问：%s（%s）' % (BOARD_URL, e), '[!!]')
            else:
                say('网页地址：%s' % BOARD_URL, '[OK]')
            return
        # 记录还在、看板却没了：上一轮半死不活，接管重来
        say('上一轮的记录还在（pid %s），但看板没在运行，重新拉起 ...'
            % ', '.join(map(str, others)), '[..]')
        for p in others:
            kill_pid(p)
        clean_pidfile('_supervisor.pid')
    write_pidfile('_supervisor.pid')

    # 1) 清掉上一轮的抓包/看板（版本更新后不能还跑着旧代码）
    say('清理上一轮残留进程 ...')
    kill_stale()

    # 2) 看板先起来，2 秒内就能看；抓包不依赖小程序是否在线，也先拉起来
    start_board()
    say('启动抓包 ...')
    start_capture(cruise)

    # 3) 小程序进程在不在
    say('检查小程序进程 ...')
    pids = wechat_pids()
    if not pids:
        say('没检测到小程序进程。请先打开微信里的「小象超市」，进到分类页。', '[!!]')
        while not wechat_pids():
            time.sleep(5)
            say('仍在等待小程序打开 ...', '[..]')
        say('检测到小程序进程', '[OK]')
    else:
        say('检测到 %d 个小程序进程' % len(pids), '[OK]')

    # 4) 调试代理
    if proxy_up():
        say('调试代理已在运行', '[OK]')
    else:
        ok = False
        for i in range(3):
            if i:
                say('10 秒后重试启动调试代理 ...', '[..]')
                time.sleep(10)
            ok = start_proxy()
            if ok or STATE['no_node']:
                break
        if not ok and STATE['no_node']:
            say('调试代理已跳过（缺 node）。看板照常可用，装好 Node.js 后重开本脚本。', '[!!]')

    if open_browser and board_up():
        try:
            webbrowser.open(BOARD_URL)
        except Exception:
            pass

    # 5) 等小程序挂上来（挂不上也继续，后面一轮轮提示并自动接住）
    say('等待小程序连上调试代理 ...')
    if wait_miniapp(timeout=25):
        say('小程序已连上（%d 个 target）' % mini_targets(force=True), '[OK]')
    else:
        say('小程序还没连上。请把它整个关掉再打开一次（连接是启动时建立的）', '[..]')

    print('-' * 62)
    if mini_targets(force=True) > 0:
        say('已就绪：现在在小程序里手动翻页 / 切分类，抓包会自动记账。', '[OK]')
    else:
        say('抓包已就位，等你把小程序关掉重开一次接上。', '[..]')
    say('看板：%s   （在小程序点分类，看板会跟着跳）' % BOARD_URL)
    say('这个窗口别关，按 Ctrl+C 结束。')
    print('-' * 62)

    last_pages = pages()
    last_stat = 0
    last_connected = None
    last_hint = 0
    last_probe = 0.0

    while True:
        time.sleep(2.0)
        now = time.time()

        # 进程守护
        if not proxy_up() and not alive('proxy'):
            if STATE['no_node']:
                # 缺 node，再拉也没用；每 60 秒轻声提醒一次就好
                if now - STATE['proxy_hint_at'] > 60:
                    STATE['proxy_hint_at'] = now
                    say('调试代理未运行（缺 node）。装好 Node.js 后重开本脚本。', '[!!]')
            else:
                say('调试代理挂了，重新拉起 ...', '[!!]')
                start_proxy()
                if wait_miniapp(timeout=20):
                    say('小程序已重新连上', '[OK]')
                else:
                    say('等你在小程序里重开一次', '[..]')
        if not alive('board'):
            say('看板挂了，重新拉起 ...', '[!!]')
            start_board()
        if not alive('capture'):
            say('抓包进程退出了，重新拉起 ...', '[!!]')
            start_capture(cruise)

        # 连接状态变化单独提醒。
        # 只在「还没连上」时探测：连上以后每 2 秒开一条短 CDP 连接去问，
        # 会和抓包端的长连接抢调试通道，让小程序会话反复重建（看起来很反常）。
        if last_connected is not True and now - last_probe > 10:
            last_probe = now
            conn = mini_targets(force=True) > 0
        else:
            conn = last_connected if last_connected is not None else False
        if conn != last_connected:
            last_connected = conn
            if conn:
                say('小程序已接上，开始记账', '[OK]')
            else:
                say('小程序断开（关掉了或在重载），等它回来 ...', '[..]')

        # 一直没接上就每半分钟提醒一次，别让它默默干等
        if not conn and now - last_hint > 30:
            last_hint = now
            say('还在等小程序连上 —— 把它整个关掉再打开一次（只退页面不够）', '[..]')

        # 每 15 秒报一次进度
        if now - last_stat > 15:
            last_stat = now
            n = pages()
            c = current()
            pos = ''
            if c.get('subName'):
                pos = c.get('subName') + (' / ' + c['thirdName'] if c.get('thirdName') else '')
            delta = n - last_pages
            last_pages = n
            say('页数 %d（%s%d）  位置 %s  %s' % (
                n, '+' if delta >= 0 else '', delta, pos or '未在分类页',
                '已接上' if conn else '未接上'), '[..]')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n正在关闭抓包与看板 ...')
        for k in ('capture', 'board'):
            kill(k)
        clean_pidfile('_supervisor.pid')
        print('调试代理保持运行（下次启动不用重开小程序）。已退出')
