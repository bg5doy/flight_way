# -*- coding: utf-8 -*-
"""
dumphfdl 诊断：判断它到底有没有「看到」HFDL 信号。

原理：用 DEBUG 版 dumphfdl（-DDEBUG），配合 --debug dsp,frame，程序退出时会打印
      A1_found / A2_found / M1_found 及其相关值（correlation）与训练比特错误率。
      · A1/A2 = HFDL 的前导/同步序列；M1 = 帧类型标识
      · 若 A1/A2/M1_found 全为 0 且相关值很低 → 信号没进来（或频率/带宽不对）
      · 若 A1_found > 0 但 M1_found = 0 → 找到了前导但解不出帧（信噪比/滤波问题）

用法：
  python diag_dumphfdl.py --device 23 --seconds 30
  python diag_dumphfdl.py --wav hfdl.wav
"""
import argparse
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

BASE = os.path.dirname(os.path.abspath(__file__))
CS16 = os.path.join(BASE, "_diag_iq.cs16")


def record(device, seconds, rate):
    import sounddevice as sd
    print(f"采集设备[{device}] {seconds}s @ {rate}Hz ...")
    x = sd.rec(int(seconds * rate), samplerate=rate, channels=1,
               device=device, dtype="float32")
    sd.wait()
    return x[:, 0].astype(np.float64), rate


def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, sw, rate, n = (w.getnchannels(), w.getsampwidth(),
                           w.getframerate(), w.getnframes())
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    else:
        a = (np.frombuffer(raw, dtype="<u1").astype(np.float64) - 128) / 128.0
    if ch > 1:
        a = a.reshape(-1, ch)[:, 0]
    return a, rate


def to_cs16(a, rate, mode=sb.MODE_ANALYTIC):
    br = sb.SoundcardBridge(mode=mode, sample_rate=rate, sink=None)
    bs = 4096
    # 需要 sink 才能写文件；这里直接用其 _convert 逻辑
    chunks = []
    for k in range(0, len(a), bs):
        i, q = br._convert(a[k:k + bs].reshape(-1, 1))
        chunks.append(sb.to_cs16(i, q))
    data = b"".join(chunks)
    with open(CS16, "wb") as f:
        f.write(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=23)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--wav")
    ap.add_argument("--mode", default=sb.MODE_ANALYTIC, choices=sb.MODES)
    ap.add_argument("--scan-from", type=float, default=400.0)
    ap.add_argument("--scan-to", type=float, default=2900.0)
    ap.add_argument("--scan-step", type=float, default=200.0)
    ap.add_argument("--debug-bin", default="/usr/local/bin/dumphfdl-debug")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--legacy-freq", action="store_true",
                    help="不做 -1440Hz 补偿（旧行为，已证明会偏 1440 Hz）")
    args = ap.parse_args()

    if args.wav:
        a, rate = read_wav(args.wav)
        print(f"读取 WAV {args.wav}: {len(a)/rate:.1f}s @ {rate}Hz")
    else:
        a, rate = record(args.device, args.seconds, args.rate)

    rms = float(np.sqrt(np.mean(a ** 2)))
    print(f"音频 RMS={rms:.5f} ({20*np.log10(max(rms,1e-12)):.1f} dBFS)")

    nb = to_cs16(a, rate, args.mode)
    print(f"已转换 CS16 I/Q: {nb} 字节 → {CS16}")

    # dumphfdl（WSL）需要 /mnt/... 路径
    wsl_iq = ohc.to_wsl_path(CS16)
    st = ohc.to_wsl_path(os.path.join(BASE, "systable.conf"))
    kind, _ = ohc.find_dumphfdl()
    binpath = args.debug_bin

    def _frange(a):
        out, f = [], a.scan_from
        while f <= a.scan_to + 1e-6:
            out.append(f)
            f += a.scan_step
        return out

    # 关键：dumphfdl 假定信号中心在 channel_freq + HFDL_SSB_CARRIER_OFFSET_HZ(1440Hz)
    #   freq_shift = centerfreq - (freq + 1440)        见 src/hfdl.c:46,476
    # 要让"绝对音频频率 A"的信号落到 0 Hz，需 centerfreq - freq = A - 1440。
    # 取 centerfreq = 1440 Hz（=1.44 kHz）即得 freq = A —— 频道频率直接填音频频率，
    # 且恒为正数（避免负频率被 dumphfdl 拒绝）。
    if args.legacy_freq:
        ctr = "0"
    else:
        ctr = "1.44"
    chans = [f"{ac/1000.0:g}" for ac in
             ([1441.0] if args.no_scan else _frange(args))]

    def _frange(a):
        out, f = [], a.scan_from
        while f <= a.scan_to + 1e-6:
            out.append(f)
            f += a.scan_step
        return out

    cmd = ["wsl", "-d", "Ubuntu-20.04", "--", binpath,
           "--iq-file", wsl_iq, "--sample-format", "CS16",
           "--sample-rate", str(rate), "--centerfreq", ctr,
           "--system-table", st,
           "--debug", "dsp,frame",
           "--output", "decoded:text:file:path=-",
           "--utc"] + chans
    print(f"运行: centerfreq={ctr} kHz, 频道 {chans[0]}~{chans[-1]} kHz "
          f"({len(chans)} 个)")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")

    print("\n===== A1/A2/M1 汇总（关键）=====")
    keys = ("A1_found", "A2_found", "M1_found", "A1_corr_avg", "A2_corr_avg",
            "M1_corr_avg", "train_bits_bad")
    found = False
    for line in out.splitlines():
        if any(k in line for k in keys):
            print("  " + line.strip())
            found = True
    if not found:
        print("  （未出现汇总——可能不是 DEBUG 版，或程序未正常退出）")

    dec = [l for l in out.splitlines() if ">" in l and "HFDL" in l]
    print(f"\n解出的帧行数: {len(dec)}")
    for l in dec[:5]:
        print("  " + l[:120])

    print("\n===== 调试输出摘要（末尾 25 行）=====")
    for line in out.splitlines()[-25:]:
        print("  " + line[:150])

    try:
        os.remove(CS16)
    except Exception:
        pass


if __name__ == "__main__":
    main()
