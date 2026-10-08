@echo off
chcp 65001 >nul
rem ============================================================
rem  把 G 盘的 GPT-SoVITS 搬运到本项目目录（自包含）
rem  可重复执行：已存在的文件会自动跳过，中断后重跑即可续传
rem  说明：/E 复制所有子目录（含空目录）——绝不要改成 /MIR，
rem        那个开关会删除目标端多余文件，很危险。
rem ============================================================

set SRC=G:\GPT-SoVITS-v2pro-20250604
set DST=F:\GradProject\GPT-SoVITS
set LOG=F:\GradProject\copy_sovits.log

echo 正在从 %SRC% 搬运到 %DST% ...
echo 首次约 16GB，预计 15-40 分钟。日志：%LOG%
echo.

robocopy "%SRC%" "%DST%" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:16 /XJ /NFL /NDL /NP /LOG:"%LOG%"

set RC=%ERRORLEVEL%
echo.
echo robocopy 退出码 = %RC%
if %RC% LSS 8 (
    echo 搬运成功（0-7 都表示成功，0 表示无文件需要复制即完全一致）
) else (
    echo 搬运出错，请查看日志：%LOG%
)
exit /b %RC%
