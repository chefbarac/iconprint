@echo off
cd /d "%~dp0"

echo Starting Icon Print Scanner...
echo.

python -u scan.py

echo.
echo Scanner service stopped.
pause