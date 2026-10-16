# -*- coding: utf-8 -*-
"""GUI 声卡桥接后端端到端校验：声卡 → CS16 I/Q → dumphfdl → 输出解析。"""
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
from config import BACKEND_OPENHFDL_SOUNDCARD, BACKEND_LABELS
from main import HFDLApp

root = tk.Tk()
app = HFDLApp(root)

app.var_backend.set(BACKEND_LABELS[BACKEND_OPENHFDL_SOUNDCARD])
app.var_bridge_rate.set("48000")
app.var_bridge_center.set("1370")
# 选第一个可用设备
if app.devices:
    app.var_bridge_dev.set(f"[{app.devices[0][0]}] {app.devices[0][1]}")

_result = {}


def run():
    try:
        root.after(300, app.start_decode)
        time.sleep(11)
        txt = app.txt_log.get("1.0", "end")
        _result["bridge_log"] = "声卡桥接" in txt or "bridge" in txt
        _result["dumphfdl_started"] = "dumphfdl" in txt
        _result["decoder_type"] = type(app.decoder).__name__ if app.decoder else None
        _result["stats"] = dict(app.decoder.stats) if app.decoder else None
        _result["bridge_samples"] = (app.decoder.bridge.stats["samples"]
                                     if (app.decoder and app.decoder.bridge) else 0)
        app.stop_decode()
        time.sleep(1)
        _result["stopped"] = app.decoder is None
    except Exception as e:
        _result["error"] = repr(e)
    finally:
        root.after(300, root.destroy)


threading.Thread(target=run, daemon=True).start()
root.mainloop()

for k in ("decoder_type", "dumphfdl_started", "bridge_samples", "stats",
          "stopped", "error"):
    print(f"BRIDGE_{k}:", _result.get(k))
ok = (_result.get("decoder_type") == "BridgedDecoder"
      and _result.get("dumphfdl_started")
      and (_result.get("bridge_samples") or 0) > 0
      and _result.get("stopped"))
print("RESULT:", "PASS" if ok else "FAIL")
