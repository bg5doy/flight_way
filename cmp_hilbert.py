# -*- coding: utf-8 -*-
"""
对比两种"实音频 -> 解析 I/Q"的实现，看是否是转换精度导致 train_bits_bad 偏高：
  fir : 当前桥接用的流式 FIR 希尔伯特
  fft : scipy.signal.hilbert（整段 FFT，理论最优，仅离线可用）
"""
import os
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soundcard_bridge as sb
import openhfdl_client as ohc
from scipy.signal import hilbert

BASE = os.path.dirname(os.path.abspath(__file__))
WAV = sys.argv[1] if len(sys.argv) > 1 else "hfdl.wav"


def load(path):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()),
                          dtype="<i2").astype(np.float64) / 32768.0
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels())[:, 0]
    return x, rate


def make(path, x, rate, conv):
    if conv == "fir":
        br = sb.SoundcardBridge(mode=sb.MODE_ANALYTIC, sample_rate=rate, sink=None)
        out = []
        bs = 4096
        for k in range(0, len(x), bs):
            i, q = br._convert(x[k:k + bs].reshape(-1, 1))
            out.append(sb.to_cs16(i, q))
        data = b"".join(out)
    else:
        # 理想解析信号（整段 FFT），并补偿 0 延迟
        z = hilbert(x - x.mean())
        data = sb.to_cs16(z.real, z.imag)
    with open(path, "wb") as f:
        f.write(data)
    return path


def run(iq, rate, from_hz, to_hz, step):
    st = ohc.to_wsl_path(os.path.join(BASE, "systable.conf"))
    chans = []
    f = from_hz
    while f <= to_hz + 1e-6:
        chans.append(f"{f/1000.0:g}")
        f += step
    cmd = ["wsl", "-d", "Ubuntu-20.04", "--", "/usr/local/bin/dumphfdl-debug",
           "--iq-file", ohc.to_wsl_path(iq), "--sample-format", "CS16",
           "--sample-rate", str(rate), "--centerfreq", "1.44",
           "--system-table", st, "--debug", "dsp,frame",
           "--output", "decoded:text:file:path=-", "--utc"] + chans
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return (r.stdout or "") + (r.stderr or "")


def summarize(tag, out):
    print(f"\n===== {tag} =====")
    for k in ("A1_found", "A2_found", "M1_found", "A1_corr_avg", "A2_corr_avg",
              "M1_corr_avg"):
        for line in out.splitlines():
            if line.startswith(k):
                print("  " + line.strip())
                break
    import re
    tb = re.findall(r"train_bits_bad: (\d+)/(\d+) \(([\d.]+)%\)", out)
    if tb:
        pct = sorted(float(p) for _, _, p in tb)
        print(f"  train_bits_bad: {len(tb)} 次, 最好 {pct[0]:.2f}%, "
              f"中位 {pct[len(pct)//2]:.2f}%, 最差 {pct[-1]:.2f}%")
    else:
        print("  train_bits_bad: 无（未进入训练阶段）")
    dec = [l for l in out.splitlines() if ">HFDL" in l or "HFDL<" in l]
    print(f"  解出帧: {len(dec)}")
    for l in dec[:4]:
        print("    " + l[:120])
    # 等化器/帧类型线索
    for l in out.splitlines():
        if "M1" in l and ("found" in l or "match" in l):
            print("    " + l[:120])


if __name__ == "__main__":
    x, rate = load(WAV)
    print(f"{WAV}: {len(x)/rate:.1f}s @ {rate}Hz")
    for conv in ("fir", "fft"):
        iq = os.path.join(BASE, f"_cmp_{conv}.cs16")
        make(iq, x, rate, conv)
        out = run(iq, rate, 1100, 2300, 100)
        summarize(f"{conv} 希尔伯特", out)
        try:
            os.remove(iq)
        except Exception:
            pass
    print("\n结论：若 fft 明显优于 fir，则桥接需改用更高精度的解析变换。")
