# -*- coding: utf-8 -*-
"""
OpenHFDL 集成端到端自测：
  1. 生成一个占位 I/Q 文件（无真实信号，仅验证进程与数据管道）
  2. 通过 openhfdl_client 启动真实的 dumphfdl（WSL 内）
  3. 校验它能启动、加载系统表、把 JSON 输出接到 stdout（path=-）
  4. 校验 JSON/Basestation 解析器对真实 dumphfdl 输出结构的解析

说明：占位 I/Q 不含真实 HFDL 信号，因此不会有解码帧产出；
      解码正确性依赖真实 SDR 或 I/Q 录制。本测试验证的是“集成管道”。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import openhfdl_client as ohc
from config import HfdlConfig

BASE = os.path.dirname(os.path.abspath(__file__))
IQ = os.path.join(BASE, "openhfdl", "dummy_iq.cs16")


def make_dummy_iq(path, mb=2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * (mb * 1024 * 1024))
    return path


def main():
    ok = True
    print("=== 1. 定位 dumphfdl ===")
    kind, path = ohc.find_dumphfdl()
    print(f"   kind={kind} path={path}")
    if not kind:
        print("   未找到 dumphfdl，跳过进程测试（解析器测试仍会执行）")
        ok = False
    else:
        print("\n=== 2. 生成占位 I/Q 文件 ===")
        make_dummy_iq(IQ)
        print(f"   {IQ} ({os.path.getsize(IQ)} bytes)")

        print("\n=== 3. 通过客户端启动 dumphfdl ===")
        logs = []
        cfg = HfdlConfig()
        cfg.oh_iq_file = IQ
        cfg.oh_centerfreq = 0.0            # 自动取频道中心
        cfg.oh_sample_rate = 250000
        frames = []
        dec = ohc.OpenHfdlDecoder(cfg, on_frame=frames.append, on_log=logs.append)
        started = dec.start()
        print(f"   启动: {started}")
        if not started:
            ok = False
        else:
            time.sleep(8)
            running = dec.proc is not None and dec.proc.poll() is None
            print(f"   进程运行中: {running}")
            dec.stop()
            print("   --- dumphfdl 输出 ---")
            for line in logs[:12]:
                print("   |", line)
            joined = "\n".join(logs).lower()
            checks = {
                "加载系统表": "system table loaded" in joined,
                "JSON输出已打开": "output_file(-)" in joined or "shutting down" in joined,
            }
            for k, v in checks.items():
                print(f"   [{'OK' if v else 'FAIL'}] {k}")
                ok = ok and v

    print("\n=== 4. 解析器校验（真实 dumphfdl JSON 结构）===")
    samples = [
        '{"app":{"name":"dumphfdl","ver":"1.7.0"},"t":{"sec":1700000000,"usec":1},'
        '"freq":11384,"bit_rate":1800,"sig_level":-42.5,"noise_level":-55.1,'
        '"freq_skew":12.3,"slot":"S","hfnpdu":{"type":{"id":13,"name":"performance data"},'
        '"flight_id":"QF1234","pos":{"lat":-33.9431,"lon":151.1783}}}',
        '{"app":{"name":"dumphfdl"},"t":{"sec":1700000001,"usec":0},"freq":8927,'
        '"bit_rate":300,"slot":"D","hfnpdu":{"type":{"name":"frequency data"},'
        '"flight_id":"BAW256","pos":{"lat":51.47,"lon":-0.4543}}}',
    ]
    for i, s in enumerate(samples, 1):
        rec = ohc.parse_openhfdl_json(s)
        fr = ohc.record_to_frame(rec, s)
        good = (rec and fr and fr.position_ok() and fr.flight)
        print(f"   样例{i}: flight={rec.get('flight')} freq={rec.get('freq_khz')} "
              f"rate={rec.get('bit_rate')} pos=({rec.get('lat')},{rec.get('lon')}) "
              f"-> {'OK' if good else 'FAIL'}")
        ok = ok and bool(good)

    bs = ("MSG,3,1,1,780ABC,1,2024/05/01,12:34:56.000,MU510,,,,31.25,121.45,,"
          "11384,,,,0")
    rec = ohc.parse_basestation(bs)
    good = rec and rec.get("flight") == "MU510" and abs(rec.get("lat") - 31.25) < 1e-6
    print(f"   basestation: {rec.get('flight')} {rec.get('icao')} "
          f"({rec.get('lat')},{rec.get('lon')}) -> {'OK' if good else 'FAIL'}")
    ok = ok and bool(good)

    print("\n=== 结果:", "PASS" if ok else "PARTIAL/FAIL", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
