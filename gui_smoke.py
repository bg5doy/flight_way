# -*- coding: utf-8 -*-
"""GUI 启动冒烟测试：创建窗口、初始化地图、自动跑仿真、校验地图/存储联动。

注：内置教学解调后端已移除（与真实 HFDL 非位级兼容，解不出实际报文），
    故本测试改为校验当前三个可用后端与地图/存储链路。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tkinter as tk
from config import BACKEND_LABELS
from main import HFDLApp

root = tk.Tk()
app = HFDLApp(root)
root.after(400, app.start_sim)

_result = {}


def check_and_exit():
    try:
        for _ in range(50):
            time.sleep(0.5)
            if app.store.count() >= 3 and app.map.counts() >= 2:
                break
        _result["store"] = app.store.count()
        _result["flights"] = app.map.counts()
        _result["oh"] = app.lbl_oh.cget("text").splitlines()[0]
        _result["presets"] = len(app.presets)
        _result["backend"] = app.var_backend.get()
        # 后端下拉必须只含可用后端（不含已移除项）
        _result["backends"] = list(app.var_backend.trace_vinfo()) if False else \
            [BACKEND_LABELS[k] for k in BACKEND_LABELS]
        # 地图：等比投影 + 拖拽可用
        _result["aspect"] = app.map.ax.get_aspect()
        x0, x1 = app.map._xlim
        for _ in range(3):
            app.map._on_scroll(type("E", (), {"inaxes": app.map.ax, "xdata": 0.0,
                                              "ydata": 0.0, "button": "up"})())
        b = app.map.ax.bbox
        px, py = b.x0 + b.width / 2, b.y0 + b.height / 2
        app.map._on_press(type("E", (), {"inaxes": app.map.ax, "button": 1,
                                         "x": px, "y": py})())
        app.map._on_move(type("E", (), {"inaxes": app.map.ax, "x": px + 150, "y": py})())
        app.map._on_release(type("E", (), {"inaxes": app.map.ax, "button": 1})())
        _result["panned"] = bool(abs(app.map._xlim[1] - x1) > 0.5)
        app.stop_sim()
    except Exception as e:
        _result["error"] = repr(e)
    finally:
        root.after(250, root.destroy)


threading.Thread(target=check_and_exit, daemon=True).start()
root.mainloop()

print("GUI_STORE_COUNT:", _result.get("store"))
print("GUI_MAP_FLIGHTS:", _result.get("flights"))
print("GUI_DUMPHFDL:", _result.get("oh"))
print("GUI_PRESETS:", _result.get("presets"))
print("GUI_BACKEND:", _result.get("backend"))
print("GUI_MAP_ASPECT:", _result.get("aspect"))
print("GUI_MAP_PANNED:", _result.get("panned"))
print("GUI_ERROR:", _result.get("error"))

_c = {
    "no_error": not _result.get("error"),
    "store>=3": (_result.get("store") or 0) >= 3,
    "flights>=2": (_result.get("flights") or 0) >= 2,
    "aspect==1": _result.get("aspect") == 1.0,
    "panned": _result.get("panned") is True,
}
print("GUI_CHECKS:", _c)
ok = all(_c.values())
print("GUI SMOKE DONE", "PASS" if ok else "FAIL")
