@echo off
setlocal
"%~dp0runtime-win64\python.exe" -B "%~dp0of.py" create-player --ask-name
set "rc=%errorlevel%"
pause
exit /b %rc%
