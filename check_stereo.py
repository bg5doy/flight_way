# -*- coding: utf-8 -*-
"""检查 IC-7300 USB 音频的左右声道关系：是否真 I/Q，还是单声道复制。"""
import sys
import numpy as np
import sounddevice as sd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEV = int(sys.argv[1]) if len(sys.argv) > 1 else 1
RATE = 48000
print(f"设备 [{DEV}] 双声道采集 3 秒 ...")
data = sd.rec(int(3 * RATE), samplerate=RATE, channels=2, device=DEV, dtype="float32")
sd.wait()
L = data[:, 0].astype(np.float64)
R = data[:, 1].astype(np.float64)

print(f"  L: RMS={np.sqrt(np.mean(L**2)):.5f}  峰值={np.max(np.abs(L)):.5f}")
print(f"  R: RMS={np.sqrt(np.mean(R**2)):.5f}  峰值={np.max(np.abs(R)):.5f}")
if np.std(R) < 1e-9:
    print("  R 声道为静音 → 单声道设备")
else:
    corr = float(np.corrcoef(L, R)[0, 1])
    diff = float(np.sqrt(np.mean((L - R) ** 2)))
    print(f"  相关系数 corr(L,R) = {corr:.6f}")
    print(f"  RMS(L-R) = {diff:.6f}  (RMS(L)={np.sqrt(np.mean(L**2)):.6f})")
    if corr > 0.999 and diff < 0.01 * np.sqrt(np.mean(L ** 2)):
        print("  => 左右完全相同：单声道复制，**不能**当 I/Q 用 → 请选 analytic 模式")
    else:
        print("  => 左右不同：可能是真 I/Q，也可能是立体声；"
              "若为声卡 SDR 正交输出可选 stereo-iq")
