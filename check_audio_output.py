# -*- coding: utf-8 -*-
"""
音频输出诊断工具 —— 检查每个输出设备能否真正打开并播放。

用途：
  · 排查「没声音」：区分是设备选错、静音、还是音频引擎卡死
  · 重启 / 换线 / 改显示器之后验证 HDMI 音频是否恢复

用法：
    python check_audio_output.py            # 全部检查（含试听 0.3 秒提示音）
    python check_audio_output.py --no-sound # 只检查能否打开，不发声

判读：
    [OK]   该设备可正常打开并播放
    [FAIL] 打不开 —— 软件侧重置（重启音频服务 / 重装设备）若无效，
           通常是驱动或音频引擎卡死，重启电脑或重装显卡驱动
"""
import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import sounddevice as sd

SR = 48000


def tone(seconds=0.3, freq=440.0):
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sound", action="store_true", help="不实际发声")
    ap.add_argument("--seconds", type=float, default=0.3)
    args = ap.parse_args()

    apis = sd.query_hostapis()
    try:
        default_out = sd.query_devices(kind="output")
        dflt = (default_out["index"], default_out["name"])
    except Exception:
        dflt = (-1, "<无>")

    print("=" * 96)
    print(f"系统默认输出设备: [{dflt[0]}] {dflt[1]}")
    print("=" * 96)

    rows, ok_n, fail_n = [], 0, 0
    wave = tone(args.seconds)
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] <= 0:
            continue
        api = apis[d["hostapi"]]["name"]
        # 跳过聚合/虚拟条目（PortAudio 的 Sound Mapper / 主声音驱动）
        if "Sound Mapper" in d["name"] or "主声音驱动" in d["name"]:
            continue
        status, detail = "?", ""
        if args.no_sound:
            try:
                st = sd.OutputStream(samplerate=SR, device=i, channels=2,
                                     dtype="float32")
                st.close()
                status = "OK"
            except Exception as e:
                status, detail = "FAIL", str(e).splitlines()[0]
        else:
            try:
                with sd.OutputStream(samplerate=SR, device=i, channels=2,
                                     dtype="float32") as st:
                    st.write(wave.reshape(-1, 1).repeat(2, axis=1))
                status = "OK"
            except Exception as e:
                status, detail = "FAIL", str(e).splitlines()[0]
        # WDM-KS 不支持 PortAudio 的阻塞式读写 —— 属工具限制，非设备故障
        if status == "FAIL" and "Blocking API not supported" in detail:
            status = "N/A"
        if status == "OK":
            ok_n += 1
        elif status == "FAIL":
            fail_n += 1
        mark = " <== 默认" if i == dflt[0] else ""
        rows.append((status, i, api, d["name"], detail, mark))

    for status, i, api, name, detail, mark in rows:
        print(f"  [{status:4}] [{i:2d}] {api:>20}  {name[:52]}{mark}")
        if detail:
            print(f"         └─ {detail[:120]}")

    print("-" * 96)
    print(f"可播放: {ok_n}    打不开: {fail_n}")

    bad_default = any(s == "FAIL" and mk for s, _, _, _, _, mk in rows)
    if bad_default:
        print("\n⚠ 系统默认输出设备打不开 —— 这就是「没声音」的直接原因。")
        print("  处置顺序：")
        print("   1) 右键任务栏喇叭 → 声音设置 → 把输出切到上面 [OK] 的设备（先恢复声音）")
        print("   2) 重启音频服务：Restart-Service Audiosrv -Force")
        print("   3) 设备管理器里禁用再启用该音频设备")
        print("   4) 重启电脑（HDMI 音频引擎卡死时最有效）")
        print("   5) 仍不行 → 重装显卡驱动（HDMI 音频随显卡驱动）")
    elif fail_n:
        print("\n部分设备打不开，但默认设备可用，声音应正常。")
        print("打不开的多为 HDMI/显卡音频引擎问题或已拔出的显示设备残留。")
    else:
        print("\n全部输出设备均正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
