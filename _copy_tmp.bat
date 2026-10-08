@echo off
robocopy "G:\GPT-SoVITS-v2pro-20250604" "F:\GradProject\GPT-SoVITS" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:16 /XJ /NFL /NDL /NP /LOG:"F:\GradProject\copy_sovits.log"
echo ROBOCOPY_EXIT=%ERRORLEVEL%
