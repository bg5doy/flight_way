# -*- coding: utf-8 -*-
"""放大渲染民航客机图标（多部件：机体 + 两台发动机），检查外形与朝向。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from worldmap import AIRCRAFT_PARTS

fig, axes = plt.subplots(1, 5, figsize=(15, 4.2), facecolor="#0a1b2c")
for ax, hdg in zip(axes, (0, 90, 180, 270, 135)):
    ang = np.radians(hdg)
    ca, sa = np.cos(ang), np.sin(ang)
    for k, part in enumerate(AIRCRAFT_PARTS):
        lx, ly = part[:, 0], part[:, 1]
        rx = lx * ca + ly * sa
        ry = -lx * sa + ly * ca
        fc = "#00e5ff" if k == 0 else "#00b8d4"
        ax.fill(rx, ry, facecolor=fc, edgecolor="#eaffff",
                lw=1.0 if k == 0 else 0.7, zorder=3)
    ax.arrow(0, 0, 0, 1.5, color="#ff5252", width=0.010, head_width=0.09,
             length_includes_head=True, zorder=5)
    ax.text(0, 1.66, "N", color="#ff5252", ha="center", fontsize=9)
    ax.set_title(f"heading {hdg}°", color="#9fd8f5", fontsize=10)
    ax.set_xlim(-1.7, 1.7)
    ax.set_ylim(-1.7, 1.7)
    ax.set_aspect("equal")
    ax.set_facecolor("#08192a")
    ax.tick_params(colors="#5a8fb0", labelsize=7)
    for s in ax.spines.values():
        s.set_color("#2a5f85")
fig.suptitle("Civil airliner icon: fuselage + wings + winglets + tail + 2 engines",
             color="#cfe8ff", fontsize=11)
fig.tight_layout()
fig.savefig("icon_outline.png", dpi=110, facecolor="#0a1b2c")
print("saved icon_outline.png  部件数:", len(AIRCRAFT_PARTS),
      " 机体顶点:", len(AIRCRAFT_PARTS[0]))
