@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Only start the voice engine (GPT-SoVITS) service.
rem  Normally you do NOT need this file -- just click
rem  "Start Service" inside the web UI.
rem
rem  Use this when the web UI cannot open and you want to
rem  check whether the voice engine itself works.
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

"%PY%" run_sovits.py
pause
