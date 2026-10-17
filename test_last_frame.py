# -*- coding: utf-8 -*-
"""验证「最新帧 (UTC)」列：显示的是该航班最后一帧的接收时刻，而不是"几秒前"。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from datetime import datetime, timezone

import tkinter as tk

import main as M
from main import HFDLApp

ok = True


def chk(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


print("=" * 66)
print("1) 时间格式化 _fmt_utc")
print("=" * 66)
ts = 1792146438          # 固定时间戳
want = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
got = M._fmt_utc(ts)
chk("格式为 HH:MM:SS", got == want, f"{got} (期望 {want})")
chk("长度 8", len(got) == 8, got)
chk("非法输入回退 —", M._fmt_utc(None) == "—")

print("\n" + "=" * 66)
print("2) 列表列内容")
print("=" * 66)
root = tk.Tk()
root.geometry("1500x900")
app = HFDLApp(root)
root.update()

# 造两架飞机，last_seen 相差 300 秒
class FF:
    def __init__(self, k, lat, lon):
        self.flight, self.reg = k, "B-1234"
        self.lat, self.lon = lat, lon
        self.heading, self.altitude, self.speed = 90.0, 35000, None
    def key(self):
        return self.flight
    def position_ok(self):
        return True

now = time.time()
a = app.map.upsert(FF("CA1704", 30.0, 100.0))
b = app.map.upsert(FF("AAL100", 32.0, 102.0))
a.last_seen = now
b.last_seen = now - 300
app._tree_keys = []
app._refresh_tree()
root.update()

cols = app.tree["columns"]
chk("列名已改为「最新帧 (UTC)」", cols[-1] == "seen"
    and app.tree.heading("seen")["text"] == "最新帧 (UTC)",
    app.tree.heading("seen")["text"])

va = app.tree.item("CA1704", "values")
vb = app.tree.item("AAL100", "values")
print(f"  CA1704 最后帧: {va[-1]}   AAL100 最后帧: {vb[-1]}")
chk("显示的是时刻而非秒数", ":" in va[-1] and ":" in vb[-1])
chk("与 last_seen 对应", va[-1] == M._fmt_utc(a.last_seen)
    and vb[-1] == M._fmt_utc(b.last_seen))
chk("两机时间差 300 秒",
    abs(int(va[-1][:2]) * 3600 + int(va[-1][3:5]) * 60 + int(va[-1][6:])
        - (int(vb[-1][:2]) * 3600 + int(vb[-1][3:5]) * 60 + int(vb[-1][6:]))
        ) in (300, 85800), f"{va[-1]} vs {vb[-1]}")

# 时间会随新帧更新
old = va[-1]
a.last_seen = now + 60
app._refresh_tree()
root.update()
new = app.tree.item("CA1704", "values")[-1]
chk("新帧到达后时间随之更新", new != old, f"{old} → {new}")

print("\n结果:", "PASS" if ok else "FAIL")
root.destroy()
sys.exit(0 if ok else 1)
