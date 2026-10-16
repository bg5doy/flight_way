# -*- coding: utf-8 -*-
"""验证 read_wav_any 能读 SDRuno 风格的 float32 立体声 I/Q WAV 与普通 PCM WAV。"""
import os
import struct
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soundcard_bridge as sb


def write_float32_stereo(path, i, q, rate):
    inter = np.empty(2 * len(i), dtype="<f4")
    inter[0::2] = i.astype(np.float32)
    inter[1::2] = q.astype(np.float32)
    data = inter.tobytes()
    fmt = struct.pack("<HHIIHH", 3, 2, rate, rate * 8, 8, 32)   # tag=3 float
    chunks = (b"fmt " + struct.pack("<I", len(fmt)) + fmt +
              b"data" + struct.pack("<I", len(data)) + data)
    hdr = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE"
    with open(path, "wb") as f:
        f.write(hdr + chunks)


def main():
    rate, n = 48000, 9600
    t = np.arange(n) / rate
    i = np.cos(2 * np.pi * 1000 * t)
    q = np.sin(2 * np.pi * 1000 * t)

    ok = True
    # ① float32 立体声 I/Q（模拟 SDRuno IQ 录制）
    write_float32_stereo("_t_f32.wav", i, q, rate)
    a, r, ch, fmt = sb.read_wav_any("_t_f32.wav")
    good = (a.shape == (n, 2) and r == rate and ch == 2 and "FLOAT" in fmt
            and abs(a[100, 0] - i[100]) < 1e-3 and abs(a[100, 1] - q[100]) < 1e-3)
    print(f"① float32 立体声 I/Q : shape={a.shape} rate={r} ch={ch} fmt={fmt} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    # ② PCM16 单声道
    with wave.open("_t_pcm.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((i * 32767).astype("<i2").tobytes())
    a2, r2, ch2, f2 = sb.read_wav_any("_t_pcm.wav")
    good = a2.shape == (n, 1) and r2 == rate and ch2 == 1 and "PCM16" in f2
    print(f"② PCM16 单声道    : shape={a2.shape} rate={r2} ch={ch2} fmt={f2} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    # ③ PCM16 立体声
    inter = np.empty(2 * n, dtype="<i2")
    inter[0::2] = (i * 32767).astype("<i2")
    inter[1::2] = (q * 32767).astype("<i2")
    with wave.open("_t_pcm2.wav", "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(inter.tobytes())
    a3, r3, ch3, f3 = sb.read_wav_any("_t_pcm2.wav")
    good = a3.shape == (n, 2) and ch3 == 2 and "PCM16" in f3
    print(f"③ PCM16 立体声    : shape={a3.shape} rate={r3} ch={ch3} fmt={f3} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    # ④ 用 stereo-iq 模式走一遍桥接的转换（确认 I/Q 直通）
    br = sb.SoundcardBridge(mode=sb.MODE_STEREO_IQ, sample_rate=rate, sink=None)
    ii, qq = br._convert(a)
    good = (abs(ii[100] - i[100]) < 1e-3 and abs(qq[100] - q[100]) < 1e-3)
    print(f"④ stereo-iq 直通   : I/Q 与源一致 -> {'OK' if good else 'FAIL'}")
    ok &= good

    for f in ("_t_f32.wav", "_t_pcm.wav", "_t_pcm2.wav"):
        try:
            os.remove(f)
        except Exception:
            pass
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
