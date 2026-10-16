# -*- coding: utf-8 -*-
"""
声卡检查工具（用于 IC-7300 等外接电台的 USB 声卡）。

用途：
  1. `--list`            列出所有输入设备及其通道数、默认采样率
  2. `--probe <idx>`     探测某设备支持的采样率（逐个实测打开）
  3. `--capture <idx>`   采集若干秒音频，输出电平/频谱峰值，并可存 PNG
                         —— 用来确认电台音频是否真的进来了、
                            以及 HFDL 信号在音频中的实际中心频率

典型用法（IC-7300）：
    python check_audio.py --list
    python check_audio.py --probe 1
    python check_audio.py --capture 1 --seconds 5 --plot ic7300_spectrum.png
"""
import argparse
import os
import sys
import time

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def list_devices():
    import sounddevice as sd
    print("=== 音频设备 ===")
    rows = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 or d["max_output_channels"] > 0:
            rows.append((i, d))
    for i, d in rows:
        tag = ""
        name = d["name"]
        if "USB Audio CODEC" in name:
            tag = "   <== 常见于 Icom IC-7300 / 通用 USB 声卡"
        print(f"[{i:2d}] 输入={d['max_input_channels']} 输出={d['max_output_channels']} "
              f"默认采样率={d['default_samplerate']:.0f} Hz\n      {name}{tag}")
    try:
        din, dout = sd.default.device
        print(f"\n默认输入设备索引 = {din}")
    except Exception:
        pass
    print("\n提示：IC-7300 在 Windows 上通常显示为 “USB Audio CODEC”，"
          "输入通道数为 1 或 2。")
    return rows


def probe_device(idx, rates=(48000, 44100, 96000, 192000, 8000, 16000, 32000, 384000)):
    import sounddevice as sd
    print(f"=== 探测设备 [{idx}] 支持的采样率 / 通道 ===")
    for ch in (1, 2):
        for sr in rates:
            try:
                sd.check_input_settings(device=idx, channels=ch,
                                        samplerate=sr, dtype="float32")
                print(f"  {sr:6d} Hz  {ch}ch  ✓ 支持")
            except Exception as e:
                msg = str(e).split(":")[-1].strip()[:60]
                print(f"  {sr:6d} Hz  {ch}ch  ✗ ({msg})")


def capture(idx, seconds=5.0, rate=48000, channels=1, plot=None, save_wav=None):
    import sounddevice as sd
    print(f"=== 采集设备 [{idx}]：{seconds} 秒 @ {rate} Hz, {channels}ch ===")
    try:
        data = sd.rec(int(seconds * rate), samplerate=rate, channels=channels,
                      device=idx, dtype="float32")
        sd.wait()
    except Exception as e:
        print(f"采集失败: {e}")
        return False

    x = data[:, 0].astype(np.float64) if data.ndim > 1 else data.astype(np.float64)
    n = len(x)
    rms = float(np.sqrt(np.mean(x ** 2)))
    pk = float(np.max(np.abs(x)))
    dbfs = 20 * np.log10(max(rms, 1e-12))
    pkdbfs = 20 * np.log10(max(pk, 1e-12))
    print(f"  采样点: {n}")
    print(f"  RMS = {rms:.5f}  ({dbfs:.1f} dBFS)")
    print(f"  峰值 = {pk:.5f}  ({pkdbfs:.1f} dBFS)")
    if pk < 1e-4:
        print("  ⚠ 几乎没有信号：检查电台是否已连接、USB 音频是否被选为输入、"
              "电台 AF/音量与 DATA 输出电平")
        return False
    if pk > 0.98:
        print("  ⚠ 可能削顶（过载）：请降低电台输出电平")

    # 频谱（Welch）
    try:
        from scipy import signal as ss
        f, P = ss.welch(x, fs=rate, nperseg=4096)
    except Exception:
        f = np.fft.rfftfreq(n, 1 / rate)
        P = np.abs(np.fft.rfft(x))[:len(f)] ** 2

    band = (f >= 200) & (f <= 3500)          # HFDL 通常落在这个音频范围
    if band.any():
        fp = f[band][int(np.argmax(P[band]))]
        print(f"  200–3500 Hz 内最强分量: {fp:.1f} Hz")
    # 能量重心（信号大致中心）
    if band.any() and P[band].sum() > 0:
        cen = float(np.sum(f[band] * P[band]) / np.sum(P[band]))
        print(f"  200–3500 Hz 能量重心:  {cen:.1f} Hz  "
              f"← 可把这个值填进 GUI 的「音频中心」")

    # ---- HFDL 信号特征判定 ----
    # HFDL 是 PSK 数据信号 → 包络接近恒定；带限噪声的包络服从瑞利分布。
    # 用包络变异系数 CV = std/mean 区分：
    #   恒定包络 PSK: CV 小(≈0.05~0.25)   噪声: CV 大(≈0.45~0.55)
    try:
        from scipy.signal import hilbert
        xf = x - np.mean(x)
        env = np.abs(hilbert(xf))
        # 只用中段，避开起止瞬态
        env = env[len(env) // 10: -len(env) // 10] if len(env) > 100 else env
        cv = float(np.std(env) / (np.mean(env) + 1e-12))
        # 谱平坦度：SSB 通带内 600–2500 Hz 的 PSD 起伏(dB)
        fb = (f >= 600) & (f <= 2500)
        if fb.any() and P[fb].max() > 0:
            PdB = 10 * np.log10(P[fb] / P[fb].max() + 1e-20)
            flat = float(np.std(PdB))
        else:
            flat = 99.0
        print(f"  包络变异系数 CV = {cv:.3f}   通带内谱起伏 = {flat:.1f} dB")
        if cv < 0.30 and flat < 4.0:
            print("  ✅ 疑似**恒定包络数据信号（可能就是 HFDL）**："
                  "包络平稳、通带内谱接近平顶")
        elif cv < 0.35:
            print("  ⚠ 包络较平稳，可能有数据信号，但谱不够平顶"
                  "（检查滤波器带宽/中心位置）")
        else:
            print("  ❌ 包络起伏大（瑞利分布特征）→ 更像**带限噪声**，"
                  "此频率上当前没有可解调的 HFDL 信号")
    except Exception as e:
        print(f"  (信号判定跳过: {e})")

    if save_wav:
        try:
            import wave
            with wave.open(save_wav, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
            print(f"  已保存 WAV: {save_wav}")
        except Exception as e:
            print(f"  保存 WAV 失败: {e}")

    if plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(2, 1, figsize=(10, 6))
            ax[0].plot(np.arange(min(n, rate)) / rate, x[:rate])
            ax[0].set_title("Waveform (first 1 s)"); ax[0].set_xlabel("s")
            ax[1].semilogy(f, P + 1e-20)
            ax[1].set_xlim(0, 4000)
            ax[1].set_title("Spectrum (0-4 kHz) — HFDL should appear as a "
                            "~2.8 kHz wide hump")
            ax[1].set_xlabel("Hz"); ax[1].grid(True, alpha=0.3)
            fig.tight_layout(); fig.savefig(plot, dpi=110)
            print(f"  已保存频谱图: {plot}")
        except Exception as e:
            print(f"  绘图失败: {e}")
    return True


def main():
    ap = argparse.ArgumentParser(description="声卡检查工具（IC-7300 等）")
    ap.add_argument("--list", action="store_true", help="列出设备")
    ap.add_argument("--probe", type=int, metavar="IDX", help="探测设备支持的采样率")
    ap.add_argument("--capture", type=int, metavar="IDX", help="采集并分析")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--channels", type=int, default=1)
    ap.add_argument("--plot", metavar="PNG", help="保存频谱图")
    ap.add_argument("--save-wav", metavar="WAV", help="保存 WAV 供离线分析")
    args = ap.parse_args()

    did = False
    if args.list or (args.probe is None and args.capture is None):
        list_devices(); did = True
    if args.probe is not None:
        probe_device(args.probe); did = True
    if args.capture is not None:
        capture(args.capture, args.seconds, args.rate, args.channels,
                args.plot, args.save_wav); did = True
    return 0 if did else 1


if __name__ == "__main__":
    sys.exit(main())
