# -*- coding: utf-8 -*-
"""音频诊断：把声音拆成数字，看它到底哪里不好。"""
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

FFMPEG = r"F:\GradProject\GPT-SoVITS\runtime\ffmpeg.exe"


def decode(path, sr=32000):
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le",
           "-acodec", "pcm_f32le", "-ar", str(sr), "-ac", "1", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[:300])
    return np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)


def frame_rms(x, frame=1024, hop=512):
    if len(x) < frame:
        return np.array([np.sqrt((x ** 2).mean()) if len(x) else 0.0])
    n = (len(x) - frame) // hop + 1
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    return np.sqrt((fr ** 2).mean(axis=1))


def f0_track(x, sr=32000, fmin=70, fmax=400, frame=1024, hop=512):
    """自相关法测基频（说话音高）。返回 (f0数组, 是否有声)."""
    n = (len(x) - frame) // hop + 1
    f0 = np.zeros(n)
    voiced = np.zeros(n, dtype=bool)
    lo = int(sr / fmax)
    hi = min(int(sr / fmin), frame - 1)
    for i in range(n):
        seg = x[i * hop: i * hop + frame]
        seg = seg - seg.mean()
        e = np.sqrt((seg ** 2).mean())
        if e < 0.002:           # 太安静，判定为无声
            continue
        ac = np.correlate(seg, seg, mode="full")[frame - 1:]
        ac = ac / (ac[0] + 1e-12)
        if hi <= lo + 1:
            continue
        window = ac[lo:hi]
        if len(window) == 0:
            continue
        k = int(np.argmax(window)) + lo
        if ac[k] > 0.3:          # 周期性够强才算有声
            f0[i] = sr / k
            voiced[i] = True
    return f0, voiced


def analyze(path, label):
    x = decode(path)
    if x.size == 0:
        return {"label": label, "file": str(path), "error": "空文件"}
    dur = len(x) / 32000.0
    peak = float(np.abs(x).max())
    rms = float(np.sqrt((x ** 2).mean()))
    fdb = 20 * np.log10(np.maximum(frame_rms(x), 1e-12))

    quiet = float(np.percentile(fdb, 20))
    # 只统计"真的接近静音"的帧，作为底噪
    floor = float(np.percentile(fdb, 5))
    loud = float(np.percentile(fdb, 95))

    # 上面两个指标会被文件首尾的"纯零填充"污染（模型会补 0.3 秒全零）。
    # 这里掐掉首尾各 0.6 秒，只统计中间真正的说话区域。
    pad = int(0.6 * 32000 / 512)
    inner = fdb[pad:len(fdb) - pad] if len(fdb) > 2 * pad + 10 else fdb
    inner_floor = float(np.percentile(inner, 10))
    inner_p25 = float(np.percentile(inner, 25))
    # 静音处的实际底噪：取中间区域最安静的 5% 的平均值
    k = max(1, len(inner) // 20)
    inner_quietest_mean = float(np.mean(np.sort(inner)[:k]))

    # 削顶（波形撞到最大值被削平）—— 听起来会"炸/破"
    clipped = float(np.mean(np.abs(x) > 0.995))
    nearclip = float(np.mean(np.abs(x) > 0.90))

    # 静音段（连续低于底噪+6dB 的区间）
    thr = floor + 6
    sil = fdb < thr
    runs = []
    start = None
    for i, s in enumerate(sil):
        if s and start is None:
            start = i
        elif not s and start is not None:
            runs.append(((i - start) * 512 / 32000.0, start * 512 / 32000.0))
            start = None
    if start is not None:
        runs.append(((len(sil) - start) * 512 / 32000.0, start * 512 / 32000.0))
    long_sil = [r for r in runs if r[0] > 0.8]

    # 突变（相邻帧能量跳变 > 25dB）—— 拼接点/爆音
    d = np.abs(np.diff(fdb))
    jumps = int(np.sum(d > 25))

    # 频谱：高频占比（沙哑、齿音、噪声都体现在这）
    spec = np.abs(np.fft.rfft(x[: 32000 * 60] * np.hanning(min(len(x), 32000 * 60)))) ** 2
    freqs = np.fft.rfftfreq(min(len(x), 32000 * 60), 1 / 32000)
    tot = spec.sum() + 1e-12
    hf = float(spec[freqs > 6000].sum() / tot)
    vhf = float(spec[freqs > 10000].sum() / tot)
    # 频谱重心
    centroid = float((freqs * spec).sum() / tot)
    # 15kHz 以上还有多少能量（32k 采样理论上限 16k）
    ultra = float(spec[freqs > 14000].sum() / tot)

    f0, voiced = f0_track(x)
    v = f0[voiced]
    if v.size > 5:
        f0_med = float(np.median(v))
        f0_std = float(np.std(v))
        f0_p5, f0_p95 = float(np.percentile(v, 5)), float(np.percentile(v, 95))
        # 音高变化的"半音"数，衡量抑扬顿挫
        semitone_range = float(12 * math.log2(f0_p95 / max(f0_p5, 1e-6)))
        voiced_ratio = float(voiced.mean())
    else:
        f0_med = f0_std = f0_p5 = f0_p95 = semitone_range = voiced_ratio = 0.0

    return {
        "label": label,
        "file": Path(path).name,
        "duration_sec": round(dur, 2),
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-12)), 1),
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-12)), 1),
        "noise_floor_dbfs_P5": round(floor, 1),
        "noise_floor_dbfs_P20": round(quiet, 1),
        "inner_noise_dbfs_P10": round(inner_floor, 1),
        "inner_noise_dbfs_P25": round(inner_p25, 1),
        "inner_quietest5pct_mean": round(inner_quietest_mean, 1),
        "loud_dbfs_P95": round(loud, 1),
        "dynamic_range_dB": round(loud - quiet, 1),
        "clip_ratio_0995": round(clipped, 5),
        "near_clip_ratio_090": round(nearclip, 5),
        "energy_jumps_gt25dB": jumps,
        "long_silences_gt0.8s": len(long_sil),
        "silence_total_sec": round(sum(r[0] for r in runs), 2),
        "hf_energy_gt6k_ratio": round(hf, 5),
        "hf_energy_gt10k_ratio": round(vhf, 6),
        "hf_energy_gt14k_ratio": round(ultra, 6),
        "spectral_centroid_Hz": round(centroid, 1),
        "f0_median_Hz": round(f0_med, 1),
        "f0_std_Hz": round(f0_std, 1),
        "f0_semitone_range_p5_p95": round(semitone_range, 2),
        "voiced_ratio": round(voiced_ratio, 3),
    }


if __name__ == "__main__":
    out = []
    for p in sys.argv[1:]:
        try:
            out.append(analyze(p, Path(p).stem))
        except Exception as e:
            out.append({"file": str(p), "error": str(e)})
    print(json.dumps(out, ensure_ascii=False, indent=1))
