@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  旧版界面启动入口（Gradio 版，备用）
rem
rem  什么时候用它：万一新版界面出问题（打不开、某功能不对），
rem  双击这个文件就能回到改造前的界面，功能都是好的。
rem  新界面入口就是旁边的 start.bat。
rem ============================================================

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set PY=python
where python >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        set PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
    ) else (
        echo.
        echo [错误] 没找到 Python。请先安装 Python 3.10 或更高版本。
        echo.
        pause
        exit /b 1
    )
)

echo.
echo 启动【旧版界面】（Gradio）…
echo.
"%PY%" main.py

echo.
echo 系统已退出。
pause
