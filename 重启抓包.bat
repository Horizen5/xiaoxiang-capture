@echo off
setlocal
cd /d "%~dp0tools"
if not exist "restart_hidden.bat" exit /b 2
call "restart_hidden.bat"
exit /b 0
