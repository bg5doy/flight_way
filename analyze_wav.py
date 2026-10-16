# -*- coding: utf-8 -*-
"""分析 WAV：频谱分布、时间结构、信号实际所在的音频频率与带宽。"""
import sys
import wave
import numpy as np
from scipy import signal as ss
from scipy.signal import hilbert

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

path = sys.argv[1] if len(sys.argv) > 1 else "hfdl.wav"
with wave.open(path, "rb") as w:
    ch, sw, rate, n = (w.getnchannels(), w.getsampwidth(),
                       w.getframerate(), w.getnframes())
    raw = w.readframes(n)
x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
if ch > 1:
    x = x.reshape(-1, ch)[:, 0]
print(f"{path}: {len(x)/rate:.1f}s @ {rate}Hz, {ch}ch")
print(f"RMS={np.sqrt(np.mean(x**2)):.5f}  峰值={np.max(np.abs(x)):.4f}  "
      f"DC={x.mean():+.6f}")

# 频谱
f, P = ss.welch(x, fs=rate, nperseg=8192)
PdB = 10 * np.log10(P + 1e-20)
print(f"\n=== 频谱（0–4000 Hz，每 200 Hz 平均功率 dB）===")
for lo in range(0, 4000, 200):
    m = (f >= lo) & (f < lo + 200)
    if m.any():
        avg = 10 * np.log10(np.mean(P[m]) + 1e-20)
        bar = "█" * max(0, int((avg + 110) / 2))
        print(f"{lo:5d}-{lo+200:5d} Hz  {avg:7.1f} dB  {bar}")

# 噪声底与信号区
noise = np.median(PdB[(f > 3600) & (f < 8000)])
print(f"\n噪声底(3.6–8k) ≈ {noise:.1f} dB")
above = np.where(PdB > noise + 6)[0]
if len(above):
    print(f"高于噪声底+6dB 的范围: {f[above[0]]:.0f} – {f[above[-1]]:.0f} Hz "
          f"(宽 {f[above[-1]]-f[above[0]]:.0f} Hz)")
else:
    print("没有任何频段高于噪声底+6dB")

# 时间结构：短时能量（HFDL 是 TDMA 突发）
print(f"\n=== 时间结构（每 0.5s 的 RMS dB）===")
blk = int(0.5 * rate)
vals = []
for i in range(0, len(x) - blk, blk):
    seg = x[i:i + blk]
    vals.append(20 * np.log10(max(np.sqrt(np.mean(seg ** 2)), 1e-12)))
vals = np.array(vals)
print("  " + " ".join(f"{v:.0f}" for v in vals[:60]))
print(f"  最小={vals.min():.1f} 最大={vals.max():.1f} 动态范围={vals.max()-vals.min():.1f} dB")

# 包络统计
e = np.abs(hilbert(x - x.mean()))
cv = float(np.std(e) / (np.mean(e) + 1e-12))
print(f"\n整体包络变异系数 CV = {cv:.3f}")
print(f"（HFDL 有信号段应接近 0.1–0.3；>0.4 说明以噪声/突发为主）")

# 简易频谱图
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(11, 7))
    ax[0].semilogy(f, P + 1e-20)
    ax[0].set_xlim(0, 4000); ax[0].set_title("PSD 0-4kHz"); ax[0].grid(alpha=.3)
    f2, t2, S = ss.spectrogram(x, fs=rate, nperseg=2048, noverlap=1536)
    ax[1].pcolormesh(t2, f2, 10*np.log10(S + 1e-20), shading="auto", cmap="viridis")
    ax[1].set_ylim(0, 4000); ax[1].set_title("Spectrogram 0-4kHz")
    ax[1].set_xlabel("s"); ax[1].set_ylabel("Hz")
    fig.tight_layout()
    out = path.rsplit(".", 1)[0] + "_analysis.png"
    fig.savefig(out, dpi=110)
    print(f"\n已保存: {out}")
except Exception as ex:
    print("绘图失败:", ex)
