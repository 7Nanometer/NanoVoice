@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Stop the voice engine (GPT-SoVITS) service.
rem  Use this if the service window got closed but the port
rem  is still occupied, or to free up GPU memory.
rem ============================================================

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        set PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
    ) else (
        echo [ERROR] Python not found. Please install Python 3.10+ first.
        pause
        exit /b 1
    )
)

"%PY%" run_sovits.py --stop
pause
