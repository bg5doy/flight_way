# -*- coding: utf-8 -*-
"""RSP 硬件抽取测试。

分两层：
  1. 推荐算法（离线）—— 每个波段推荐的抽取倍数必须同时满足两条硬约束：
        · 抽取后采样率 ≥ 模拟带宽（否则带外混叠）
        · 抽取后采样率的一半 > 最远频道偏移（dumphfdl 要求频道落在半带宽内）
  2. 真机验证（有设备才跑）—— 抽取后实际输出采样率应 ≈ fs / 抽取倍数
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import hfdl_presets as P
import sdr_rsp as R

ok = True


def chk(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


print("=" * 72)
print("1) 推荐算法：各波段抽取倍数是否同时满足两条硬约束")
print("=" * 72)
print(f"  {'波段':<11}{'中心':<7}{'带宽':<7}{'抽取':<6}{'输出kSPS':<10}"
      f"{'半带宽':<8}{'最远偏移':<9}{'合法'}")
for name, ch in P.band_channels():
    c = P.recommend_center_khz(ch)
    bw = P.recommend_bw_khz(ch, c)
    d = P.recommend_decim(2000000, bw, ch, c)
    fs_eff = 2000000 / d
    half = fs_eff / 2e3
    off = max(abs(f - c) for f in ch)
    good = (fs_eff >= bw * 1e3) and (half > off)
    print(f"  {name:<11}{c:<7}{bw:<7}{str(d)+'×':<6}{fs_eff/1e3:<10.0f}"
          f"{half:<8.0f}{off:<9.0f}{'OK' if good else 'FAIL'}")
    ok &= good
chk("全部波段抽取推荐合法", ok)

print("\n  DECIM_OPTIONS =", R.DECIM_OPTIONS)
chk("1× 表示不抽取", P.recommend_decim(2000000, 1536, None, None) == 1,
    "带宽 1536 kHz 时无法抽取（fs/d < BW）")
chk("窄带宽可抽到上限", P.recommend_decim(2000000, 200, [], None) >= 4)

print("\n" + "=" * 72)
print("2) 真机：抽取后实际输出速率")
print("=" * 72)
devs, note = R.list_devices()
if not devs:
    print("  SKIP 未检测到 RSP 设备")
else:
    def measure(decim, sec=2.0):
        buf, lock = [], threading.Lock()
        src = R.RspSource(device_index=0, sample_rate=2000000,
                          freq_khz=21964.0, bw_khz=200, if_khz=0,
                          agc=2, gain_gr_db=20, lna_state=0, decim=decim,
                          log=lambda s: None)
        try:
            def cb(ba):
                with lock:
                    buf.append(ba)
            if not src.open() or not src.start(cb):
                return None
            t0 = time.time()
            time.sleep(sec)
            src.stop()
            el = time.time() - t0
        finally:
            src.close()
        with lock:
            d = b"".join(buf)
        return (len(d) // 4) / el

    r1 = measure(1)
    r8 = measure(8)
    if r1 is None or r8 is None:
        chk("采集可用", False)
    else:
        print(f"  1×: {r1/1e3:.0f} kSPS    8×: {r8/1e3:.0f} kSPS    "
              f"比值 {r1/max(r8,1):.2f}（期望 ≈8）")
        chk("8× 输出速率约为 1/8", 6.5 <= r1 / max(r8, 1) <= 9.5,
            f"{r1/max(r8,1):.2f}")
        chk("8× 输出接近 250 kSPS", 180e3 <= r8 <= 300e3, f"{r8/1e3:.0f} kSPS")

print("\n结果:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
