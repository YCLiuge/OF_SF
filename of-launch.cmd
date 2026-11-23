@echo off
"%~dp0OverField-Server\runtime-win64\python.exe" -B "%~dp0client_tools\launcher_cli.py" %*
exit /b %errorlevel%
