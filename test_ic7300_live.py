# -*- coding: utf-8 -*-
"""
用 IC-7300 的 USB 声卡做真实 HFDL 解码尝试。

流程：IC-7300 USB 音频 → 希尔伯特解析 I/Q → dumphfdl stdin → 解析输出
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

import soundcard_bridge as scb
import openhfdl_client as ohc
from config import HfdlConfig

DEVICE = int(os.environ.get("IC7300_DEV", "1"))
RATE = 48000
CENTER = float(os.environ.get("IC7300_CENTER", "1441"))
SECONDS = float(os.environ.get("IC7300_SECONDS", "120"))

cfg = HfdlConfig()
kind, path = ohc.find_dumphfdl()
SCAN = os.environ.get("IC7300_SCAN", "1") == "1"
print(f"dumphfdl: {kind} {path}")
print(f"声卡设备={DEVICE} 采样率={RATE} 音频中心={CENTER} Hz "
      f"自动搜索={'开' if SCAN else '关'} 时长={SECONDS}s")
if SCAN:
    print(f"等价命令: dumphfdl --iq-file - --sample-format CS16 "
          f"--sample-rate {RATE} --centerfreq 0 --system-table systable.conf "
          f"--output decoded:json:file:path=- --utc "
          f"0.4 0.6 0.8 1 1.2 1.4 ... 2.8")
else:
    print(f"等价命令: dumphfdl --iq-file - --sample-format CS16 "
          f"--sample-rate {RATE} --centerfreq 0 --system-table systable.conf "
          f"--output decoded:json:file:path=- --utc {CENTER/1000.0:g}")
print("-" * 70)

frames = []
logs = []
dec = scb.BridgedDecoder(
    cfg,
    on_frame=lambda fr: (frames.append(fr),
                         print(f"[FRAME] {fr.flight}/{fr.reg} "
                               f"({fr.lat},{fr.lon}) {fr.text[:70]}", flush=True)),
    on_log=lambda s: (logs.append(s), print(s, flush=True)),
    mode=scb.MODE_ANALYTIC,
    audio_center_hz=CENTER,
    soundcard_rate=RATE,
    device_index=DEVICE,
    binary=path,
    kind=kind,
    scan=SCAN,
)

if not dec.start():
    print("启动失败")
    sys.exit(1)

t0 = time.time()
try:
    while time.time() - t0 < SECONDS and dec.running.is_set():
        time.sleep(2)
        br = dec.bridge
        if br:
            print(f"  [{time.time()-t0:5.0f}s] 已灌入 {br.stats['samples']} 采样, "
                  f"dumphfdl 输出行={dec.stats['lines']}, 帧={dec.stats['frames']}",
                  flush=True)
except KeyboardInterrupt:
    pass
finally:
    dec.stop()

print("-" * 70)
print(f"结果：灌入 {dec.bridge.stats['samples'] if dec.bridge else 0} 采样，"
      f"解码帧数 = {dec.stats['frames']}")
print(f"解析出航班记录 = {len(frames)}")
for fr in frames[:10]:
    print(f"  {fr.flight}/{fr.reg} ({fr.lat},{fr.lon}) {fr.text[:80]}")
print("VERDICT:", "DECODED" if frames else "NO_FRAME")
