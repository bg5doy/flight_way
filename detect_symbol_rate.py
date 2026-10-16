# -*- coding: utf-8 -*-
"""
符号率检测：判断音频里是否存在 1800 baud 的 PSK 信号（HFDL 的符号率）。

原理：PSK 信号的**包络平方 |z(t)|²** 具有循环平稳性，其频谱在**符号率处有谱线**
      （因为符号跳变造成包络周期性起伏）。若音频真是 HFDL，应在 1800 Hz
      （或其谐波 900/3600 Hz）出现明显谱线。

这个判据完全独立于 dumphfdl、独立于频道频率设置、独立于解调实现——
只反映"音频里有没有 1800 baud 的数据信号"。
"""
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
    rate = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    if w.getnchannels() > 1:
        x = x.reshape(-1, w.getnchannels())[:, 0]

print(f"{path}: {len(x)/rate:.1f}s @ {rate} Hz")

# 解析信号
z = hilbert(x - x.mean())
env = np.abs(z) ** 2
env = env - env.mean()

# 包络谱（Welch）
f, P = ss.welch(env, fs=rate, nperseg=16384)
band = (f >= 100) & (f <= 5000)
fb, Pb = f[band], P[band]
# 归一化到中位数
med = np.median(Pb)
PdB = 10 * np.log10(Pb / med + 1e-20)

print(f"\n=== 包络谱峰值（100–5000 Hz，相对中位数的 dB）===")
idx = ss.find_peaks(PdB, height=6.0, distance=20)[0]
peaks = sorted([(PdB[i], fb[i]) for i in idx], reverse=True)[:12]
for v, fq in peaks:
    tag = ""
    if abs(fq - 1800) < 40:
        tag = "  <== 1800 baud 符号率!"
    elif abs(fq - 900) < 40:
        tag = "  <== 900 (符号率/2)"
    elif abs(fq - 3600) < 60:
        tag = "  <== 3600 (2×符号率)"
    print(f"  {fq:7.1f} Hz   +{v:5.1f} dB{tag}")
if not peaks:
    print("  （100–5000 Hz 内没有超过 +6 dB 的谱线）")

def line_strength(target, tol=60):
    m = (fb >= target - tol) & (fb <= target + tol)
    return float(PdB[m].max()) if m.any() else -99.0

print(f"\n=== 关键频点谱线强度 ===")
for t in (900, 1200, 1800, 2400, 3600):
    s = line_strength(t)
    verdict = "有" if s > 6 else ("弱" if s > 3 else "无")
    print(f"  {t:5d} Hz : +{s:5.1f} dB  -> {verdict}")

print("\n=== 解释 ===")
print("  HFDL 符号率 = 1800 baud。若 1800 Hz 处谱线 > +6 dB，说明音频里")
print("  确实存在 1800 baud 的数据信号；若 < +3 dB，则该音频不含 HFDL 波形")
print("  （可能只是带限噪声 / 载波 / 其它调制）。")

# 对照：用同样方法测一段已知的合成 1800-baud PSK，确认方法有效
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
try:
    import demod as dm
    rng = np.random.default_rng(0)
    bits = rng.integers(0, 2, 1800 * 3 * 4).astype(np.uint8)   # 4 秒
    ref = dm.modulate(bits, 1800, "8PSK", 1441.0, rate, noise_db=25.0, seed=1)
    z2 = hilbert(ref - ref.mean())
    e2 = np.abs(z2) ** 2
    e2 = e2 - e2.mean()
    f2, P2 = ss.welch(e2, fs=rate, nperseg=16384)
    m2 = (f2 >= 1740) & (f2 <= 1860)
    m2b = (f2 >= 100) & (f2 <= 5000)
    med2 = np.median(P2[m2b])
    s2 = 10 * np.log10(P2[m2].max() / med2 + 1e-20)
    print(f"\n[对照] 合成 1800baud 8PSK 参考信号：1800 Hz 谱线 = +{s2:.1f} dB")
except Exception as e:
    print("对照测试失败:", e)
