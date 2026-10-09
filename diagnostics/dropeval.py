# -*- coding: utf-8 -*-
"""统计漏字率：把听写结果和原文做对齐，看少念了多少、多念了什么。"""
import difflib
import json
import re
from pathlib import Path

import librosa
from funasr import AutoModel

M = Path(r"F:\GradProject\GPT-SoVITS\tools\asr\models")
OUT = Path(r"C:\Users\Administrator\AppData\Local\Temp\gpdiag\drop")
meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))

model = AutoModel(
    model=str(M / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
    vad_model=str(M / "speech_fsmn_vad_zh-cn-16k-common-pytorch"),
    punc_model=str(M / "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
    device="cpu", disable_update=True,
)

norm = lambda t: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t)

summary = {}
for label, info in meta.items():
    src = norm(info["text"])
    lines = []
    for i in range(info["n"]):
        f = OUT / f"{label}_{i}.wav"
        wav, _ = librosa.load(str(f), sr=16000, mono=True)
        heard = norm(model.generate(input=wav, batch_size_s=60)[0]["text"])
        sm = difflib.SequenceMatcher(None, src, heard)
        # 原文中被漏掉的连续片段
        missing = [src[j1:j2] for tag, i1, i2, j1, j2 in sm.get_opcodes()
                   if tag in ("delete", "replace") and j2 - j1 >= 3]
        extra = [heard[i1:i2] for tag, i1, i2, j1, j2 in sm.get_opcodes()
                 if tag in ("insert", "replace") and i2 - i1 >= 3]
        lines.append({
            "run": i, "duration": round(len(wav) / 16000.0, 1),
            "chars": f"{len(src)}→{len(heard)}",
            "similarity": round(sm.ratio(), 3),
            "missing": missing, "extra": extra, "heard": heard,
        })
    ok = sum(1 for x in lines if x["similarity"] >= 0.985)
    summary[label] = {"source": info["text"], "runs": lines,
                      "clean_runs": f"{ok}/{len(lines)}"}
    print("=" * 76)
    print(f"[{label}]  完全正确 {ok}/{len(lines)} 遍   原文 {len(src)} 字")
    for x in lines:
        flag = "OK " if x["similarity"] >= 0.985 else "!! "
        print(f"  {flag}第{x['run']}遍 相似度{x['similarity']}  漏:{x['missing']}  多:{x['extra']}")
        if x["similarity"] < 0.985:
            print(f"       听到: {x['heard']}")

(OUT / "eval.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
