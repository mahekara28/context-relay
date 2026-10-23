@echo off
setlocal
cd /d "%~dp0"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" run.py %*
    goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 run.py %*
    goto finished
)
python run.py %*
:finished
if errorlevel 1 (
    echo.
    echo Context Relay could not start. Python 3.10 or newer is required.
    echo If port 8765 is busy, run: start.cmd serve --port 8766
    pause
)
endlocal
