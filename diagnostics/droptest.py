# -*- coding: utf-8 -*-
"""漏字复现测试：同样内容，长块 vs 短块，各跑 4 遍看漏字率。"""
import json
from pathlib import Path

import requests

G = Path(r"F:\GradProject\GPT-SoVITS")
OUT = Path(r"C:\Users\Administrator\AppData\Local\Temp\gpdiag\drop")
OUT.mkdir(parents=True, exist_ok=True)

REF = G / "logs/7Nanometer_v2/5-wav32k/clean_32k.wav_0000004480_0000104320.wav"
REF_TXT = "大有克阳这个名字大家应该并不陌生."

FULL = ("你还记得你第一次看《星际牛仔》是什么时候吗？我跟你讲，我最近才真正把这部片子从头到尾看了一遍。"
        "看完之后我整个人是懵的，就那种，你懂吗？像是被人往心口打了一拳，但是又不疼，就是闷闷的，"
        "酸酸的，特别想找人聊聊。")

LONG = FULL[:100]     # 100 字 —— 代码里的块上限
SHORT = FULL[:50]     # 50 字

s = requests.Session()
s.trust_env = False

CASES = [
    ("长100字_温度0.8", LONG, "all_zh", dict(top_k=30, top_p=0.8, temperature=0.8)),
    ("短50字_温度0.8", SHORT, "zh", dict(top_k=30, top_p=0.8, temperature=0.8)),
]

meta = {}
for label, text, lang, kw in CASES:
    for i in range(4):
        payload = {
            "text": text, "text_language": lang,
            "refer_wav_path": str(REF), "prompt_text": REF_TXT, "prompt_language": "zh",
            "speed": 1.0, "sample_steps": 32, "cut_punc": "", **kw,
        }
        r = s.post("http://127.0.0.1:9880/", json=payload, timeout=300)
        p = OUT / f"{label}_{i}.wav"
        p.write_bytes(r.content)
        print("生成", p.name, len(r.content), "字节")
    meta[label] = {"text": text, "lang": lang, "kwargs": kw, "n": 4}

(OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("LONG =", LONG)
print("SHORT =", SHORT)
