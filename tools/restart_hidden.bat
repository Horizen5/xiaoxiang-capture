@echo off
setlocal
cd /d "C:\Users\Administrator\WorkBuddy\2026-10-17-17-27-18\tools"
if not exist "start_beta.py" exit /b 2

set "PY="
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if not defined PY set "PY=python"

echo 正在重启抓包服务（旧进程会一起停掉，调试代理保留复用）...
start "" /min "%PY%" -u start_beta.py --daemon --restart
echo 看板地址 http://127.0.0.1:8899/ ，就绪后会自动打开网页。
ping -n 6 127.0.0.1 >nul
exit /b 0
