@echo off
echo 正在启动GPT-SoVITS服务...
cd /d G:\GPT-SoVITS-v2pro-20250604
echo 当前目录: %cd%

set PYTHON_EXE=G:\GPT-SoVITS-v2pro-20250604\runtime\python.exe
set S2_MODEL=G:\GPT-SoVITS-v2pro-20250604\GPT_SoVITS\pretrained_models\v2Pro\s2Gv2Pro.pth
set S1_MODEL=G:\GPT-SoVITS-v2pro-20250604\GPT_SoVITS\pretrained_models\s1v3.ckpt
set REF_AUDIO=G:\GPT-SoVITS-v2pro-20250604\logs\7Nanometer\5-wav32k\阿基拉.mp3_0000000000_0000310400.wav

echo 启动命令: %PYTHON_EXE% api.py -d cuda -s "%S2_MODEL%" -g "%S1_MODEL%" -dr "%REF_AUDIO%" -dt "测试语音克隆" -dl "zh"
%PYTHON_EXE% api.py -d cuda -s "%S2_MODEL%" -g "%S1_MODEL%" -dr "%REF_AUDIO%" -dt "测试语音克隆" -dl "zh" 2>&1