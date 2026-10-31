@echo off
setlocal
title XiaoXiang Market - Capture beta

if not exist "%~dp0tools\start_beta.py" goto nofile
cd /d "%~dp0tools"

set "PY="
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if defined PY goto run
set "PY=python"

:run
"%PY%" -u start_beta.py %*
echo.
echo Done. Press any key to close.
pause >nul
exit /b 0

:nofile
echo Cannot find tools\start_beta.py next to this file.
pause >nul
