# -*- coding: utf-8 -*-
"""诊断：列出每个音频设备及其所属主机 API，确认重复枚举问题。"""
import sys
import sounddevice as sd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

apis = sd.query_hostapis()
print("=== 主机 API ===")
for i, a in enumerate(apis):
    print(f"[{i}] {a['name']:22s} 默认输入={a.get('default_input_device')} "
          f"默认输出={a.get('default_output_device')} 设备数={len(a['devices'])}")

print("\n=== 输入设备（含所属主机 API）===")
print(f"{'idx':>4} {'hostapi':>12} {'ch':>3}  {'name'}")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        api = apis[d["hostapi"]]["name"]
        print(f"{i:>4} {api:>12} {d['max_input_channels']:>3}  {d['name']}")

print("\n=== 按 (名称, 输入通道数) 分组，看重复情况 ===")
from collections import defaultdict
groups = defaultdict(list)
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        groups[(d["name"], d["max_input_channels"])].append(
            (i, apis[d["hostapi"]]["name"]))
dups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"总输入设备 {sum(len(v) for v in groups.values())} 个，"
      f"唯一 {len(groups)} 个，重复组 {len(dups)} 个：")
for (name, ch), lst in dups.items():
    print(f"  {name!r} ({ch}ch): " + ", ".join(f"[{i}]{a}" for i, a in lst))
