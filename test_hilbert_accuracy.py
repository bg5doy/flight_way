# -*- coding: utf-8 -*-
"""
检验希尔伯特解析信号变换在 HFDL 音频频段（400–2900 Hz @ 48 kHz）的精度。

关键指标：
  · 镜像抑制 (image rejection)：应 < -40 dB
  · 幅度平坦度：解析信号包络应恒定（纯音输入）
  · I/Q 正交性：Q 相对 I 应恰好超前 90°
短 FIR 希尔伯特在低频（归一化频率很小）处过渡带占比大，精度会显著下降。
"""
import sys
import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import soundcard_bridge as sb
from scipy.signal import hilbert

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FS = 48000
FREQS = [300, 400, 600, 800, 1000, 1370, 1441, 1800, 2200, 2600, 2900, 3200]


def fir_metrics(n_taps):
    """用 n_taps 抽头 FIR 希尔伯特，测各频点的镜像抑制 / 幅度 / 相位。"""
    hi = sb.HilbertIQ(n=n_taps)
    rows = []
    t = np.arange(FS * 2) / FS
    for f in FREQS:
        hi2 = sb.HilbertIQ(n=n_taps)      # 每个频点用新滤波器，避免瞬态串扰
        x = np.cos(2 * np.pi * f * t)
        I, Q = hi2.process(x)
        I, Q = I[FS // 2:], Q[FS // 2:]   # 取稳态段
        z = I + 1j * Q
        S = np.abs(np.fft.fft(z))
        fq = np.fft.fftfreq(len(S), 1 / FS)
        pos = S[(fq > f - 50) & (fq < f + 50)].max()
        neg = S[(fq < -f + 50) & (fq > -f - 50)].max()
        img = 20 * np.log10(neg / pos + 1e-15)
        env = np.sqrt(I ** 2 + Q ** 2)
        rip = 20 * np.log10(env.max() / max(env.min(), 1e-15))
        # 用希尔伯特解析信号作参考比较相位误差
        ref = hilbert(x)[FS // 2:]
        ph_err = np.degrees(np.angle(z * np.conj(ref))).std()
        rows.append((f, img, rip, ph_err))
    return rows


def fft_metrics():
    """FFT 法（scipy.signal.hilbert）作为精度上界参考。"""
    rows = []
    t = np.arange(FS * 2) / FS
    for f in FREQS:
        x = np.cos(2 * np.pi * f * t)
        z = hilbert(x)[FS // 2:]
        S = np.abs(np.fft.fft(z))
        fq = np.fft.fftfreq(len(S), 1 / FS)
        pos = S[(fq > f - 50) & (fq < f + 50)].max()
        neg = S[(fq < -f + 50) & (fq > -f - 50)].max()
        img = 20 * np.log10(neg / pos + 1e-15)
        env = np.abs(z)
        rip = 20 * np.log10(env.max() / max(env.min(), 1e-15))
        rows.append((f, img, rip, 0.0))
    return rows


def show(title, rows):
    print(f"\n=== {title} ===")
    print(f"{'f(Hz)':>7} {'镜像抑制(dB)':>13} {'幅度起伏(dB)':>13} {'相位误差(°)':>12}")
    for f, img, rip, pe in rows:
        flag = ""
        if img > -35:
            flag += "  <镜像差>"
        if rip > 1.0:
            flag += "  <幅度差>"
        print(f"{f:>7} {img:>13.1f} {rip:>13.3f} {pe:>12.2f}{flag}")


if __name__ == "__main__":
    show("FFT 法（上界参考）", fft_metrics())
    for n in (127, 255, 511, 1023, 2047):
        show(f"当前 FIR 希尔伯特 n={n}", fir_metrics(n))
    print("\n结论：HFDL 音频在 400–2900 Hz；若低频段镜像抑制 > -35 dB 或幅度起伏 > 1 dB，")
    print("      解析信号会失真，PSK 星座被破坏 → dumphfdl 只能得到很弱的前导相关值。")
