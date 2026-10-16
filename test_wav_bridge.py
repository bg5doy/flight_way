# -*- coding: utf-8 -*-
"""用修正后的桥接（centerfreq=1.44kHz）回放 WAV，验证能否解出帧。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soundcard_bridge as sb
import openhfdl_client as ohc
from config import HfdlConfig

WAV = sys.argv[1] if len(sys.argv) > 1 else "hfdl.wav"
STEP = float(os.environ.get("BRIDGE_STEP", "100"))
FROM = float(os.environ.get("BRIDGE_FROM", "1100"))
TO = float(os.environ.get("BRIDGE_TO", "2300"))

cfg = HfdlConfig()
kind, path = ohc.find_dumphfdl()
print(f"dumphfdl: {kind} {path}")
print(f"WAV={WAV} 扫描 {FROM}-{TO} Hz 步进 {STEP} Hz")

frames = []
dec = sb.BridgedDecoder(
    cfg,
    on_frame=lambda fr: (frames.append(fr),
                         print(f"[FRAME] {fr.flight}/{fr.reg} ({fr.lat},{fr.lon}) "
                               f"{fr.text[:80]}", flush=True)),
    on_log=lambda s: print("  " + s, flush=True),
    mode=sb.MODE_ANALYTIC,
    audio_center_hz=1441.0,
    soundcard_rate=48000,
    binary=path, kind=kind,
    scan=True, scan_from=FROM, scan_to=TO, scan_step=STEP,
)
dec.wav_file = WAV

if not dec.start():
    print("启动失败")
    sys.exit(1)

t0 = time.time()
while time.time() - t0 < 180:
    time.sleep(2)
    if dec.proc and dec.proc.poll() is not None:
        break
    st = dec.bridge.stats if dec.bridge else {}
    print(f"  [{time.time()-t0:4.0f}s] 灌入 {st.get('samples',0)} 采样, "
          f"帧={dec.stats['frames']}", flush=True)

time.sleep(2)
dec.stop()
print("-" * 60)
print(f"解码帧数 = {dec.stats['frames']}  去重跳过 = {dec.stats.get('dupes',0)}")
print(f"解析出记录 = {len(frames)}")
for fr in frames[:15]:
    print(f"  {fr.flight or '?'}/{fr.reg or '?'} ({fr.lat},{fr.lon}) {fr.text[:90]}")
print("VERDICT:", "DECODED" if frames else "NO_FRAME")
