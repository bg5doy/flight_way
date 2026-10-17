# -*- coding: utf-8 -*-
"""验证地图重绘节流：无新帧时降低重绘频率，有新帧时立即重绘。

重绘很贵（matplotlib 绘制长时间占 GIL），会拖慢 RSP 写线程导致队列溢出。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tkinter as tk

from main import HFDLApp

ok = True


def chk(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


root = tk.Tk()
root.geometry("1400x860")
app = HFDLApp(root)
root.update()

# 统计 map.refresh 调用次数
cnt = {"n": 0}
orig = app.map.refresh


def counting_refresh():
    cnt["n"] += 1
    return orig()


app.map.refresh = counting_refresh

print("=" * 66)
print("1) 无新帧时：重绘应被节流（约每 5 秒 1 次）")
print("=" * 66)
cnt["n"] = 0
t0 = time.time()
while time.time() - t0 < 7.0:
    root.update()
    app._tick()
    time.sleep(0.05)
no_frame_draws = cnt["n"]
print(f"  7 秒内重绘 {no_frame_draws} 次")
chk("无新帧时重绘次数明显少于每秒一次", no_frame_draws <= 3,
    f"{no_frame_draws} 次 / 7s")

print("\n" + "=" * 66)
print("2) 有新帧时：应立刻重绘")
print("=" * 66)


class FF:
    def __init__(self, k, lat, lon):
        self.flight, self.reg = k, "B-1234"
        self.lat, self.lon = lat, lon
        self.heading, self.altitude, self.speed = 90.0, 35000, 480.0
        self.ts = time.time()
        self.raw = b""
        self.mode = 300
        self.icao = "780A1E"
        self.crc_ok = True
        self.text = "test frame"

    def key(self):
        return self.flight

    def position_ok(self):
        return True


cnt["n"] = 0
app.frame_q.put(FF("CA1704", 30.0, 100.0))
app._tick()                     # 有帧 → 应重绘
root.update()
chk("收到新帧后立即重绘", cnt["n"] >= 1, f"{cnt['n']} 次")

cnt["n"] = 0
app._tick()                     # 紧接着又一 tick，无新帧且未到 5 秒
root.update()
chk("紧随其后的空 tick 不重绘", cnt["n"] == 0, f"{cnt['n']} 次")

print("\n结果:", "PASS" if ok else "FAIL")
root.destroy()
sys.exit(0 if ok else 1)
