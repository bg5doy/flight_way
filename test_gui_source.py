# -*- coding: utf-8 -*-
"""验证 GUI 的「信号来源」切换：SDR I/Q 时强制 stereo-iq、启用 VFO、禁用带内搜索。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tkinter as tk
import soundcard_bridge as scb
from main import HFDLApp

root = tk.Tk()
app = HFDLApp(root)
root.update()
print("来源选项:", app.var_bridge_source.get())
for k in scb.SOURCES:
    app.var_bridge_source.set(scb.SOURCE_LABELS[k])
    app._on_source_change()
    root.update()
    print(f"  {k:10s} mode={app.var_bridge_mode.get()[:26]:28s}"
          f" vfo={str(app.ent_iq_vfo.cget('state')):9s}"
          f" scan={str(app.chk_scan.cget('state')):9s}")
print("提示:", app.lbl_bridge.cget("text").replace("\n", " | ")[:160])
root.destroy()
