# -*- coding: utf-8 -*-
"""端到端场景：只喂位置点（不给航向），验证航迹推算出的机头指向是否正确。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import tkinter as tk

import worldmap as wm
from worldmap import WorldMap, initial_bearing, haversine_km


class F:
    """只带位置（HFDL 实况：报文不含航向）。"""

    def __init__(self, k, lat, lon):
        self.flight, self.reg = k, "B-1234"
        self.lat, self.lon = lat, lon
        self.heading = None          # 关键：不给航向
        self.altitude, self.speed = None, None

    def key(self):
        return self.flight

    def position_ok(self):
        return True


BASE = os.path.dirname(os.path.abspath(__file__))
root = tk.Tk()
root.geometry("1300x660")
m = WorldMap(root, os.path.join(BASE, "world_ne110m.geojson"))
root.update()

# 六架飞机，各按不同方位飞；只喂一串位置点
SPECS = [("N000", 44.0, -6.0, 0.0), ("E090", 41.0, 2.0, 90.0),
         ("S180", 48.0, 12.0, 180.0), ("W270", 44.0, 22.0, 270.0),
         ("NE045", 36.0, -3.0, 45.0), ("SE135", 51.0, 2.0, 135.0)]

print("推算出的航向：")
for name, lat0, lon0, brg in SPECS:
    # 从起点沿 brg 反向铺 30 个点，最后一点是当前位置
    pts = []
    for i in range(30):
        step_km = (29 - i) * 18.0            # 每点间隔 ~18km
        dlat = step_km * np.cos(np.radians(brg)) / 111.19
        dlon = step_km * np.sin(np.radians(brg)) / (111.19 * np.cos(np.radians(lat0)))
        pts.append((lat0 - dlat, lon0 - dlon))
    for la, lo in pts:
        st = m.upsert(F(name, la, lo))
    err = abs((st.heading - brg + 180) % 360 - 180) if st.heading is not None else 999
    print(f"  {name}: 期望 {brg:5.1f}°  推算 {st.heading if st.heading is None else f'{st.heading:5.1f}°'}"
          f"  误差 {err:4.1f}°  来源={st.heading_src}  {'OK' if err < 5 else 'FAIL'}")

m.set_show_tracks(True)
m.set_track_minutes(960)
m.set_show_labels(True)
m.icon_base_px = 16
m._xlim = (-15.0, 30.0)
m._ylim = (30.0, 56.0)
m._apply()
m.refresh()
root.update()
time.sleep(0.4)
m.fig.savefig("icon_scene.png", dpi=110, facecolor="#0a1b2c")
print("\nsaved icon_scene.png")
print("航向统计 (已知, 航迹推算, 未知):", m.heading_stats())
root.destroy()
