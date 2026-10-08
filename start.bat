@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  一键启动：先自检，再打开系统
rem  %~dp0 表示"这个 bat 文件自己所在的目录"，
rem  所以整个项目文件夹搬到哪都能双击运行。
rem ============================================================

rem 让 Python 用 UTF-8 输出，避免中文乱码
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem 找一个能用的 Python：优先用 PATH 里的，找不到再试常见安装位置
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
        echo        下载地址：https://www.python.org/downloads/
        echo        安装时记得勾选 "Add Python to PATH"。
        echo.
        pause
        exit /b 1
    )
)

echo.
echo 第一步：环境自检...
echo.
"%PY%" check_env.py
if errorlevel 1 (
    echo.
    echo 自检没有全部通过，请先按上面的提示处理。
    echo 如果只是"注意"项，可以按任意键继续启动。
    echo.
    pause
)

echo.
echo 第二步：启动系统...
echo 浏览器会自动打开界面，关闭本窗口即可退出系统。
echo.
"%PY%" main.py

echo.
echo 系统已退出。
pause
