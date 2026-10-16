# -*- coding: utf-8 -*-
"""最终校验：通过 GUI 面板启动真实 OpenHFDL（I/Q 回放模式），确认端到端可用。"""
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
from config import BACKEND_OPENHFDL_LOCAL, BACKEND_LABELS
from main import HFDLApp

BASE = os.path.dirname(os.path.abspath(__file__))
IQ = os.path.join(BASE, "openhfdl", "dummy_iq.cs16")
os.makedirs(os.path.dirname(IQ), exist_ok=True)
with open(IQ, "wb") as fh:
    fh.write(b"\x00" * (2 * 1024 * 1024))

root = tk.Tk()
app = HFDLApp(root)

# 配置为 OpenHFDL 本地 + I/Q 回放
app.var_backend.set(BACKEND_LABELS[BACKEND_OPENHFDL_LOCAL])
app.var_iq.set(IQ)
app.var_ofmt.set("json")
app.var_ohsr.set("250000")
app.txt_freqs.delete("1.0", "end")
app.txt_freqs.insert("1.0", "8825 8834 8843 8885 8894 8912 8921 8927")

_result = {}


def run():
    try:
        root.after(300, app.start_decode)
        time.sleep(9)
        logs = []
        # 从日志控件抓取内容
        txt = app.txt_log.get("1.0", "end")
        _result["log_has_dumphfdl"] = "dumphfdl" in txt
        _result["log_has_systable"] = "System table loaded" in txt
        _result["decoder_active"] = app.decoder is not None
        app.stop_decode()
        time.sleep(1)
        _result["stopped"] = app.decoder is None
    except Exception as e:
        _result["error"] = repr(e)
    finally:
        root.after(300, root.destroy)


threading.Thread(target=run, daemon=True).start()
root.mainloop()

print("GUI_LAUNCH_dumphfdl_seen:", _result.get("log_has_dumphfdl"))
print("GUI_LAUNCH_systable_seen:", _result.get("log_has_systable"))
print("GUI_LAUNCH_decoder_active:", _result.get("decoder_active"))
print("GUI_LAUNCH_stopped_clean:", _result.get("stopped"))
print("GUI_LAUNCH_error:", _result.get("error"))
ok = (_result.get("log_has_systable") and _result.get("decoder_active")
      and _result.get("stopped") and not _result.get("error"))
print("RESULT:", "PASS" if ok else "FAIL")
