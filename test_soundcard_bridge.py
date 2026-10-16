# -*- coding: utf-8 -*-
"""
声卡 → OpenHFDL 桥接自测：
  1. 希尔伯特解析信号变换正确性（正频保留、负频抑制、I/Q 正交）
  2. CS16 打包格式正确
  3. 把 CS16 流经 stdin 灌入真实 dumphfdl，确认其接受并运行
"""
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soundcard_bridge as sb

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def test_hilbert():
    print("=== 1. 希尔伯特解析信号 ===")
    fs = 48000
    # 单边实信号：1370 Hz 正弦（模拟 SSB 音频中的 HFDL 音）
    t = np.arange(fs) / fs
    x = np.cos(2 * np.pi * 1370 * t)
    hi = sb.HilbertIQ()
    I, Q = hi.process(x)
    # 稳态段（跳过滤波器瞬态）
    I, Q = I[500:], Q[500:]
    # 解析信号应为复指数：I≈cos, Q≈sin
    env = np.sqrt(I ** 2 + Q ** 2)
    print(f"   包络均值={env.mean():.4f} (期望≈1.0) 标准差={env.std():.4f}")
    # 正交性/相位：Q 应超前 I 90°
    ph = np.angle(I + 1j * Q)
    dph = np.unwrap(ph)
    freq_est = np.mean(np.diff(dph)) * fs / (2 * np.pi)
    print(f"   估计频率={freq_est:.1f} Hz (期望≈1370)")
    # 负频抑制：解析信号频谱在负频应很小
    S = np.abs(np.fft.fft(I + 1j * Q))
    f = np.fft.fftfreq(len(S), 1 / fs)
    pos = S[(f > 1000) & (f < 2000)].max()
    neg = S[(f < -1000) & (f > -2000)].max()
    sup = 20 * np.log10(neg / pos + 1e-12)
    print(f"   镜像抑制={sup:.1f} dB (越负越好)")
    ok = (abs(freq_est - 1370) < 5) and (sup < -30) and (abs(env.mean() - 1) < 0.05)
    print(f"   -> {'OK' if ok else 'FAIL'}")
    return ok


def test_cs16():
    print("\n=== 2. CS16 打包 ===")
    i = np.array([0.0, 1.0, -1.0, 0.5])
    q = np.array([1.0, 0.0, -0.5, -1.0])
    b = sb.to_cs16(i, q)
    arr = np.frombuffer(b, dtype="<i2")
    print("   字节数:", len(b), "(期望 16)")
    print("   交织值:", arr.tolist())
    ok = (len(b) == 16 and arr[0] == 0 and arr[1] == 32767
          and arr[6] == 16383 and arr[7] == -32767)
    print(f"   -> {'OK' if ok else 'FAIL'}")
    return ok


def test_pipe_into_dumphfdl():
    print("\n=== 3. CS16 流 → dumphfdl stdin ===")
    import openhfdl_client as ohc
    kind, path = ohc.find_dumphfdl()
    if not kind:
        print("   未找到 dumphfdl，跳过")
        return False
    print(f"   dumphfdl: {kind} {path}")

    cmd = sb.build_stdin_command(48000, 1370.0, kind=kind, binary=path)
    print("   命令:", " ".join(cmd))

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    bridge = sb.SoundcardBridge(mode=sb.MODE_ANALYTIC, sample_rate=48000,
                                sink=proc.stdin, log=lambda s: None)
    # 送入 2 秒合成音频（含 1370 Hz 分量）
    fs = 48000
    t = np.arange(fs) / fs
    rng = np.random.default_rng(0)
    audio = 0.3 * rng.normal(0, 1, len(t)) + 0.5 * np.cos(2 * np.pi * 1370 * t)
    # 分块喂入，模拟实时
    bs = 4096
    for k in range(0, len(audio) - bs, bs):
        bridge.feed(audio[k:k + bs].reshape(-1, 1))
    for _ in range(8):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()
    out = b""
    try:
        out = proc.stdout.read() or b""
    except Exception:
        pass
    text = out.decode("utf-8", "ignore")
    print("   --- dumphfdl 输出 ---")
    for line in text.splitlines()[:10]:
        print("   |", line)
    ok = ("System table loaded" in text or "output_file(-)" in text)
    # 不应出现格式/参数错误
    bad = ("Sample format must be specified" in text
           or "Sample rate must be greater" in text
           or "too far away from the center" in text
           or "Invalid" in text)
    ok = ok and not bad
    print(f"   灌入 {bridge.stats['samples']} 采样 / {bridge.stats['bytes']} 字节")
    print(f"   -> {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    r1 = test_hilbert()
    r2 = test_cs16()
    r3 = test_pipe_into_dumphfdl()
    print("\n=== 结果:", "PASS" if (r1 and r2 and r3) else "FAIL", "===")
    sys.exit(0 if (r1 and r2 and r3) else 1)
