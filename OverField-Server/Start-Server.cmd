@echo off
setlocal
"%~dp0runtime-win64\python.exe" -B "%~dp0of.py" %*
set "rc=%errorlevel%"
if not "%rc%"=="0" if "%~1"=="" pause
exit /b %rc%
