# -*- coding: utf-8 -*-
"""生成桌面启动脚本 start_hidden.bat。

cmd 按系统 ANSI（GBK）读批处理文件，用 UTF-8 存会让中文提示乱码，
所以这个文件固定用 GBK + CRLF 写出。
"""
import os

LINES = [
    '@echo off',
    'setlocal',
    'cd /d "C:\\Users\\Administrator\\WorkBuddy\\2026-10-17-17-27-18\\tools"',
    'if not exist "start_beta.py" exit /b 2',
    '',
    'set "PY="',
    'for /d %%D in ("%USERPROFILE%\\.workbuddy\\binaries\\python\\versions\\*") '
    'do if exist "%%D\\python.exe" set "PY=%%D\\python.exe"',
    'if not defined PY set "PY=python"',
    '',
    'set "URL=http://127.0.0.1:8899/"',
    '',
    'rem 已经在跑就直接把网页打开，不重复起服务',
    'curl -s -o NUL -m 2 "%URL%" >nul 2>&1',
    'if not errorlevel 1 (',
    '  echo 抓包服务已在运行。',
    '  start "" "%URL%"',
    '  echo 已启动网页：%URL%',
    '  ping -n 4 127.0.0.1 >nul',
    '  exit /b 0',
    ')',
    '',
    'rem 没在跑就后台起一套，看板就绪后自动开网页',
    'start "" /min "%PY%" -u start_beta.py --daemon',
    'echo 正在启动抓包服务，看板就绪后会自动打开网页。',
    'echo 看板地址 %URL%',
    'ping -n 5 127.0.0.1 >nul',
    'exit /b 0',
]


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'start_hidden.bat')
    with open(out, 'w', encoding='gbk', newline='\r\n') as f:
        f.write('\n'.join(LINES) + '\n')
    print('已写入 %s（GBK + CRLF，%d 字节）' % (out, os.path.getsize(out)))


if __name__ == '__main__':
    main()
