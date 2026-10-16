# -*- coding: utf-8 -*-
"""
环回自测：modulate() → Demodulator → FrameAssembler。
验证合成 HFDL 帧经完整调制/解调链后可正确解析出航班与位置。

用法：python test_loopback.py
"""
import sys, time
import struct
import numpy as np

sys.path.insert(0, r"D:\Temp\aihfdl")

from config import HfdlConfig
from demod import Demodulator, modulate, frame_to_bits
from hfdl_frame import FrameAssembler, crc16_ccitt_false, SYNC


def build_frame(flight="MU510", reg="B2416", lat=31.25, lon=121.45,
                heading=275, fl=350, spd=480, long=True, text="RCV"):
    """构造 288-bit 长帧（36 字节）。

    帧布局：
      [0:2]   sync F3 F3
      [2]     mode (0x01=长帧)
      [3]     label ('A')
      [4:10]  注册号 6 ASCII
      [10:16] 航班号 6 ASCII
      [16:20] 纬度  float32 大端
      [20:24] 经度  float32 大端
      [24:26] 航向  uint16 大端 (度)
      [26:28] 高度  uint16 大端 (英尺)
      [28:30] 速度  uint16 大端 (节)
      [30:34] 文本  4 ASCII
      [34:36] CRC-16
    """
    mode = 0x01 if long else 0x00
    label = ord("A")
    reg_b = reg.ljust(6, " ")[:6].encode("ascii")
    flt_b = flight.ljust(6, " ")[:6].encode("ascii")
    body = bytearray([SYNC, SYNC, mode, label])
    body += reg_b + flt_b
    body += struct.pack(">ff", float(lat), float(lon))
    body += struct.pack(">HHH", int(heading) % 360, int(fl) * 100, int(spd))
    body += text.ljust(4, " ")[:4].encode("ascii")
    total = 36 if long else 9
    body = body[:total - 2]
    body += b" " * (total - 2 - len(body))
    crc = crc16_ccitt_false(bytes(body))
    return bytes(body) + crc.to_bytes(2, "big")


def run_case(sym_rate, mod_name, f0, noise_db, freq_off, descram, frame_bytes, label):
    cfg = HfdlConfig(symbol_rate=sym_rate, modulation=mod_name,
                     center_freq=f0, descrambler=descram, sample_rate=48000,
                     bandpass=False)
    demod = Demodulator(cfg, log=lambda s: print("  ", s))
    fa = FrameAssembler(cfg, on_frame=None)

    # 前导（供载波/定时捕获）+ 帧比特。载波环捕获约需 400 符号，
    # 因此前导至少给足 500 符号（低波特率下按秒数换算会不足）。
    rng = np.random.default_rng(42)
    k = {"8PSK": 3, "QPSK": 2, "4PSK": 2, "BPSK": 1}.get(mod_name.upper(), 3)
    pre_syms = max(int(sym_rate * 1.2), 520)     # 至少 520 符号
    pre_len = pre_syms * k
    pre = rng.integers(0, 2, pre_len).astype(np.uint8)
    frame_bits = frame_to_bits(frame_bytes)
    all_bits = np.concatenate([pre, frame_bits])

    audio = modulate(all_bits, sym_rate, mod_name, f0, cfg.sample_rate,
                     noise_db=noise_db, freq_offset=freq_off, seed=11)

    got = []
    bs = 2400
    t0 = time.time()
    for i in range(0, len(audio), bs):
        b = demod.push(audio[i:i + bs])
        if len(b):
            got.extend(b.tolist())
    frames = fa.push_bits(np.array(got, dtype=np.uint8))
    ok = [f for f in frames if f.crc_ok]
    dt = time.time() - t0
    print(f"{label}: bits={len(got)} frames={len(frames)} crc_ok={len(ok)} "
          f"time={dt:.2f}s")
    for f in ok:
        print(f"   flight={f.flight!r} reg={f.reg!r} pos=({f.lat},{f.lon}) "
              f"hdg={f.heading} alt={f.altitude} spd={f.speed}")
    return len(ok)


if __name__ == "__main__":
    fr = build_frame()
    print("frame hex:", fr.hex())

    cases = [
        # 标签,                波特率, 调制,   中心Hz, 噪声dB, 频偏Hz, 解扰
        ("8PSK 2400baud 30dB 0Hz  ", 2400, "8PSK", 1370, 30.0, 0.0, False),
        ("QPSK 1800baud 30dB 0Hz  ", 1800, "QPSK", 1500, 30.0, 0.0, False),
        ("8PSK 2400baud 35dB +10Hz", 2400, "8PSK", 1370, 35.0, 10.0, False),
        # 新增档位：QPSK 低速 + BPSK（对应 HFDL 300/600 bps 的调制）
        ("BPSK  300baud 30dB 0Hz  ", 300, "BPSK", 1370, 30.0, 0.0, False),
        ("QPSK  300baud 30dB 0Hz  ", 300, "QPSK", 1370, 30.0, 0.0, False),
        ("BPSK  600baud 30dB 0Hz  ", 600, "BPSK", 1370, 30.0, 0.0, False),
        ("QPSK  600baud 30dB 0Hz  ", 600, "QPSK", 1370, 30.0, 0.0, False),
        ("QPSK 1200baud 30dB 0Hz  ", 1200, "QPSK", 1370, 30.0, 0.0, False),
        ("BPSK 1200baud 30dB 0Hz  ", 1200, "BPSK", 1370, 30.0, 0.0, False),
        ("BPSK 2400baud 30dB 0Hz  ", 2400, "BPSK", 1370, 30.0, 0.0, False),
    ]
    total_ok = 0
    total = len(cases)
    for label, sr, md, f0, nd, fo, dr in cases:
        total_ok += run_case(sr, md, f0, nd, fo, dr, fr, label)

    print(f"\n=== 结果：成功解析 {total_ok}/{total} 个测试用例的帧 ===")
    print("（覆盖 BPSK/QPSK/8PSK × 300/600/1200/1800/2400 baud）")
