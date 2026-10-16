# -*- coding: utf-8 -*-
"""
符号率检测（延迟相乘 / 循环平稳法）——判断音频里数据信号的真实波特率。

原理：PSK 信号满足 z[n] ≈ z[n-L]·e^{jΔθ}（L = 每符号采样数），因此
      C(L) = |mean( z[n] · conj(z[n-L]) )| / mean(|z|²)
      会在 L = 符号周期处出现明显峰值（并在整数倍处重复）。
      该方法不依赖载波恢复、不依赖频道设置，直接测出波特率。

对 HFDL：期望峰值在 L = fs/1800。
若峰值出现在别的 L（例如 fs/1636），说明音频时间基准不对（采样率不符）。
"""
import sys
import wave
import numpy as np
from scipy.signal import butter, sosfilt
from scipy.signal import hilbert

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

path = sys.argv[1] if len(sys.argv) > 1 else "hfdl.wav"
with wave.open(path, "rb") as w:
    rate = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    if w.getnchannels() > 1:
        x = x.reshape(-1, w.getnchannels())[:, 0]
print(f"{path}: {len(x)/rate:.1f}s @ {rate} Hz")


def symbol_rate_scan(x, rate, lo_baud=400, hi_baud=4000, bp=(200, 3500)):
    """在 [lo_baud, hi_baud] 内扫描，返回 (lag, baud, score) 数组。"""
    # 带通 + 解析信号（只保留正频），避免直流与带外干扰
    nyq = rate / 2
    sos = butter(4, [bp[0] / nyq, min(bp[1], nyq * 0.95) / nyq],
                 btype="band", output="sos")
    xb = sosfilt(sos, x)
    z = hilbert(xb)
    z = z - z.mean()
    p = np.mean(np.abs(z) ** 2) + 1e-20
    lags = np.arange(int(rate / hi_baud), int(rate / lo_baud) + 1)
    scores = np.empty(len(lags))
    for i, L in enumerate(lags):
        if L <= 0 or L >= len(z):
            scores[i] = 0.0
            continue
        c = np.mean(z[L:] * np.conj(z[:-L]))
        scores[i] = abs(c) / p
    return lags, scores


def report(tag, x, rate):
    lags, scores = symbol_rate_scan(x, rate)
    bauds = rate / lags
    k = int(np.argmax(scores))
    print(f"\n=== {tag} ===")
    print(f"  最强峰值: 周期 L={lags[k]} 采样  -> 波特率 = {bauds[k]:.1f} baud"
          f"  (score={scores[k]:.4f})")
    # 前 6 个局部极大
    from scipy.signal import find_peaks
    pk, _ = find_peaks(scores, distance=3)
    order = pk[np.argsort(scores[pk])[::-1]][:6]
    print("  前几个局部峰值：")
    for i in sorted(order, key=lambda i: lags[i]):
        print(f"    L={lags[i]:3d}  {bauds[i]:7.1f} baud   score={scores[i]:.4f}"
              + ("   <== 接近 1800 baud" if abs(bauds[i] - 1800) < 30 else ""))
    return bauds[k], scores[k]


if __name__ == "__main__":
    b, s = report("实测音频", x, rate)

    # 对照：合成 1800 baud 8PSK，验证方法能测出 1800
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    try:
        import demod as dm
        rng = np.random.default_rng(0)
        bits = rng.integers(0, 2, 1800 * 3 * 6).astype(np.uint8)
        ref = dm.modulate(bits, 1800, "8PSK", 1441.0, rate, noise_db=25.0, seed=1)
        report("[对照] 合成 1800 baud 8PSK", ref, rate)
    except Exception as e:
        print("对照失败:", e)

    print("\n=== 结论 ===")
    print("  若实测峰值接近 1800 baud -> 音频里确有 1800 baud 数据信号（HFDL 符号率）")
    print("  若峰值偏离（如 ~1600）  -> 采样率/时间基准不对，需检查声卡实际采样率")
    print("  若曲线平坦无明显峰       -> 音频里没有 PSK 数据信号")
