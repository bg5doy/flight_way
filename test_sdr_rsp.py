# -*- coding: utf-8 -*-
"""SDRplay RSP 支持测试。

分三层，可在无硬件 / 无 API 的机器上优雅跳过：
  1. 结构体布局校验 —— 与 gcc 编译官方头文件得到的 sizeof/offsetof **黄金值**逐一比对
  2. API 可用性     —— DLL 加载、版本、设备枚举
  3. 真机采集       —— 抓 1 秒 I/Q，校验样本数/幅度/频谱（无设备则跳过）

黄金值来源：用 WSL gcc 编译 D:/HAM/SDRplay/API/inc 下的官方头文件，
程序见 _check_layout.c（sizeof/offsetof 输出），保证 ctypes 绑定与 C 完全一致。
"""
import ctypes as C
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import sdr_rsp as R

# ---- gcc 编译官方头文件得到的权威布局（golden values）----
GOLDEN_SIZES = {
    "DeviceT": 96, "FsFreqT": 16, "SyncUpdateT": 8, "ResetFlagsT": 3,
    "Rsp1aParamsT": 2, "Rsp2ParamsT": 1, "RspDuoParamsT": 4, "RspDxParamsT": 12,
    "DevParamsT": 64, "GainValuesT": 12, "GainT": 24, "RfFreqT": 16,
    "DcOffsetTunerT": 12, "TunerParamsT": 72, "DcOffsetT": 2,
    "DecimationT": 3, "AgcT": 20, "ControlParamsT": 32,
    "Rsp1aTunerParamsT": 1, "Rsp2TunerParamsT": 16,
    "RspDuoResetSlaveFlagsT": 2, "RspDuoTunerParamsT": 16,
    "RspDxTunerParamsT": 4, "RxChannelParamsT": 144, "DeviceParamsT": 24,
    "StreamCbParamsT": 20, "GainCbParamT": 16, "EventParamsT": 16,
    "CallbackFnsT": 24,
}

GOLDEN_OFFSETS = {
    ("DeviceT", "hwVer"): 64, ("DeviceT", "tuner"): 68,
    ("DeviceT", "rspDuoMode"): 72, ("DeviceT", "valid"): 76,
    ("DeviceT", "rspDuoSampleFreq"): 80, ("DeviceT", "dev"): 88,
    ("DevParamsT", "ppm"): 0, ("DevParamsT", "fsFreq"): 8,
    ("DevParamsT", "syncUpdate"): 24, ("DevParamsT", "resetFlags"): 32,
    ("DevParamsT", "mode"): 36, ("DevParamsT", "samplesPerPkt"): 40,
    ("DevParamsT", "rsp1aParams"): 44, ("DevParamsT", "rsp2Params"): 46,
    ("DevParamsT", "rspDuoParams"): 48, ("DevParamsT", "rspDxParams"): 52,
    ("TunerParamsT", "bwType"): 0, ("TunerParamsT", "ifType"): 4,
    ("TunerParamsT", "loMode"): 8, ("TunerParamsT", "gain"): 12,
    ("TunerParamsT", "rfFreq"): 40, ("TunerParamsT", "dcOffsetTuner"): 56,
    ("GainT", "gRdB"): 0, ("GainT", "LNAstate"): 4,
    ("GainT", "minGr"): 8, ("GainT", "gainVals"): 12,
    ("AgcT", "enable"): 0, ("AgcT", "setPoint_dBfs"): 4,
    ("AgcT", "attack_ms"): 8, ("AgcT", "decay_threshold_dB"): 14,
    ("AgcT", "syncUpdate"): 16,
    ("ControlParamsT", "dcOffset"): 0, ("ControlParamsT", "decimation"): 2,
    ("ControlParamsT", "agc"): 8, ("ControlParamsT", "adsbMode"): 28,
    ("RxChannelParamsT", "tunerParams"): 0,
    ("RxChannelParamsT", "ctrlParams"): 72,
    ("RxChannelParamsT", "rsp1aTunerParams"): 104,
    ("RxChannelParamsT", "rsp2TunerParams"): 108,
    ("RxChannelParamsT", "rspDuoTunerParams"): 124,
    ("RxChannelParamsT", "rspDxTunerParams"): 140,
    ("CallbackFnsT", "StreamACbFn"): 0,
    ("CallbackFnsT", "StreamBCbFn"): 8,
    ("CallbackFnsT", "EventCbFn"): 16,
}

ok = True


def check(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


print("=" * 70)
print("1) 结构体布局校验（对照 gcc 编译官方头文件的黄金值）")
print("=" * 70)
bad = 0
for name, want in sorted(GOLDEN_SIZES.items()):
    cls = getattr(R, name, None)
    if cls is None:
        check(f"{name} 存在", False)
        bad += 1
        continue
    got = C.sizeof(cls)
    if got != want:
        check(f"{name} sizeof", False, f"got={got} want={want}")
        bad += 1
check(f"全部 {len(GOLDEN_SIZES)} 个结构体 sizeof 一致", bad == 0,
      f"(不符 {bad} 项)")

bad = 0
for (cls_name, field), want in GOLDEN_OFFSETS.items():
    cls = getattr(R, cls_name)
    got = getattr(cls, field).offset
    if got != want:
        check(f"{cls_name}.{field} offset", False, f"got={got} want={want}")
        bad += 1
check(f"全部 {len(GOLDEN_OFFSETS)} 个字段 offset 一致", bad == 0,
      f"(不符 {bad} 项)")

print("\n" + "=" * 70)
print("2) SDRplay API 可用性")
print("=" * 70)
dll = R.find_dll()
check("找到 sdrplay_api.dll", dll is not None, dll or "未安装 SDRplay API")
if dll:
    avail, msg = R.api_info()
    check("API 可用", avail, msg)
    devs, note = R.list_devices()
    print(f"  设备数量: {len(devs)}")
    for d in devs:
        print(f"    [{d['index']}] {d['hw_name']}  SN={d['serial']}  "
              f"tuner={d['tuner']}")
    if note:
        print("  说明: " + note.replace("\n", "\n        "))
else:
    devs = []

print("\n" + "=" * 70)
print("3) 真机 I/Q 采集" + ("（无设备，跳过）" if not devs else ""))
print("=" * 70)
if not devs:
    print("  SKIP 未检测到 RSP 设备")
else:
    FS = 2000000
    blocks = []
    logs = []
    src = R.RspSource(device_index=0, sample_rate=FS, freq_khz=8927.0,
                      bw_khz=1536, if_khz=0, agc=2, gain_gr_db=40,
                      lna_state=0, log=lambda s: logs.append(s))
    got_iq = False
    try:
        if src.open():
            if src.start(lambda ba: blocks.append(ba)):
                t0 = time.time()
                time.sleep(1.0)
                src.stop()
                el = time.time() - t0
                data = b"".join(blocks)
                n = len(data) // 4
                print(f"  采集 {el:.2f}s / {len(blocks)} 块 / {n:,} 样本 "
                      f"({n/el/1e6:.3f} MSPS)")
                got_iq = n > FS * el * 0.5
                check("样本率达到预期", got_iq)
                if n > 1000:
                    iq = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    i, q = iq[0::2], iq[1::2]
                    rms = float(np.sqrt(np.mean(i ** 2)))
                    print(f"  I/Q RMS = {rms:.1f} / "
                          f"{np.sqrt(np.mean(q ** 2)):.1f}   非零率 "
                          f"{np.mean((i != 0) | (q != 0)) * 100:.1f}%")
                    check("I/Q 幅度非零", rms > 1.0)
                    m = min(n, 262144)
                    z = (i[:m] + 1j * q[:m]) * np.hanning(m)
                    sp = np.abs(np.fft.fftshift(np.fft.fft(z)))
                    floor = float(np.median(sp)) + 1e-9
                    print(f"  频谱 峰值/底噪 = "
                          f"{20*np.log10((sp.max()+1e-9)/floor):.1f} dB")
                    check("频谱有可用动态范围", sp.max() / floor > 3.0)
        else:
            check("打开设备", False, "; ".join(logs[-2:]))
    finally:
        try:
            src.close()
        except Exception:
            pass

print("\n结果:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
