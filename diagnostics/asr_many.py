# -*- coding: utf-8 -*-
"""对任意音频做"听写"，用来判断念得清不清楚、有没有漏字。"""
import re
import sys
from pathlib import Path

import librosa
from funasr import AutoModel

M = Path(r"F:\GradProject\GPT-SoVITS\tools\asr\models")
model = AutoModel(
    model=str(M / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
    vad_model=str(M / "speech_fsmn_vad_zh-cn-16k-common-pytorch"),
    punc_model=str(M / "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
    device="cpu", disable_update=True,
)


def run(path):
    wav, _ = librosa.load(str(path), sr=16000, mono=True)
    res = model.generate(input=wav, batch_size_s=60)
    return res[0]["text"], len(wav) / 16000.0


for p in sys.argv[1:]:
    try:
        text, dur = run(p)
        print("=" * 74)
        print(f"[{Path(p).stem}]  {dur:.1f}s")
        print(f"  {text}")
    except Exception as e:
        print(f"[{p}] 失败 {e}")
