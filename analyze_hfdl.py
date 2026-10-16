# -*- coding: utf-8 -*-
"""长时间分块分析：判断音频里到底有没有 HFDL 数据信号（恒包络、平顶 ~2.4kHz）。"""
import sys
import numpy as np
import sounddevice as sd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from scipy import signal as ss
from scipy.signal import hilbert

DEV = int(sys.argv[1]) if len(sys.argv) > 1 else 1
SEC = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
RATE = 48000
print(f"设备[{DEV}] 采集 {SEC}s @ {RATE}Hz，分块分析 ...")
x = sd.rec(int(SEC * RATE), samplerate=RATE, channels=1, device=DEV, dtype="float32")
sd.wait()
x = x[:, 0].astype(np.float64)
print(f"总采样 {len(x)}  RMS={np.sqrt(np.mean(x**2)):.5f}  峰值={np.max(np.abs(x)):.5f}\n")

print(f"{'块':>3} {'RMS(dBFS)':>9} {'CV包络':>7} {'平顶宽(Hz)':>10} {'中心(Hz)':>9} {'判定':>8}")
blk = int(2.0 * RATE)
for i in range(0, len(x) - blk, blk):
    seg = x[i:i + blk]
    rms = np.sqrt(np.mean(seg ** 2))
    db = 20 * np.log10(max(rms, 1e-12))
    e = np.abs(hilbert(seg - seg.mean()))
    cv = float(np.std(e) / (np.mean(e) + 1e-12))
    f, P = ss.welch(seg, fs=RATE, nperseg=4096)
    m = (f >= 200) & (f <= 3400)
    fb, Pb = f[m], P[m]
    if len(Pb) > 10 and Pb.max() > 0:
        PdB = 10 * np.log10(Pb / Pb.max() + 1e-20)
        # 平顶区：相对峰值 -3dB 以内的连续带
        above = PdB > -3.0
        if above.any():
            idx = np.where(above)[0]
            width = fb[idx[-1]] - fb[idx[0]]
            center = (fb[idx[-1]] + fb[idx[0]]) / 2
        else:
            width, center = 0.0, 0.0
    else:
        width, center = 0.0, 0.0
    verdict = "疑似HFDL" if (cv < 0.35 and width > 1500) else "噪声/其它"
    print(f"{i//blk:3d} {db:9.1f} {cv:7.3f} {width:10.0f} {center:9.0f} {verdict:>8}")
print("\n说明：HFDL 是恒包络 PSK，理想 CV≈0.1~0.25 且平顶宽≈2400Hz。")
print("      CV>0.4 或平顶宽<1000Hz → 当前没有可解调的 HFDL 信号。")
