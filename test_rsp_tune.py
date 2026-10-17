# -*- coding: utf-8 -*-
"""验证 RSP 参数优化：自动优化按钮、带宽推荐、默认值、启动告警。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tkinter as tk

import hfdl_presets as P
import sdr_rsp as rsp
from config import HfdlConfig
from main import HFDLApp

ok = True


def chk(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


print("=" * 70)
print("1) 默认值（依据 A/B 实测：AGC 开、带宽收窄）")
print("=" * 70)
cfg = HfdlConfig()
chk("AGC 默认开启(50Hz)", cfg.rsp_agc == 2, str(cfg.rsp_agc))
chk("带宽默认 300 kHz", cfg.rsp_bw_khz == 300, str(cfg.rsp_bw_khz))
chk("中心默认避开频道", cfg.rsp_freq_khz == 8903, str(cfg.rsp_freq_khz))

print("\n" + "=" * 70)
print("2) 带宽推荐 recommend_bw_khz")
print("=" * 70)
for name, ch in P.band_channels():
    c = P.recommend_center_khz(ch)
    bw = P.recommend_bw_khz(ch, c)
    halfspan = max(abs(f - c) for f in ch)
    covers = bw / 2.0 >= halfspan
    chk(f"{name:<10} 中心 {c:5d} → 带宽 {bw:>4} kHz"
        f"（最远偏离 {halfspan:3.0f} kHz）", covers)

print("\n" + "=" * 70)
print("3) GUI 自动优化按钮")
print("=" * 70)
root = tk.Tk()
app = HFDLApp(root)
root.update()

# 先复现用户场景：中心设成频道本身 21928
app.var_rsp_freq.set("21928")
app.var_rsp_bw.set("1536")
app._update_rsp_hint()
before = app.lbl_rsp.cget("text")
chk("21928 时提示 DC 重合", "DC 尖峰" in before)
chk("21928 时提示带宽可收窄", ("收窄" in before) or ("不够覆盖" in before))

# 点自动优化
app.auto_tune_rsp()
root.update()
after = app.lbl_rsp.cget("text")
fc = float(app.var_rsp_freq.get())
bw = int(app.var_rsp_bw.get())
print(f"  自动优化后：中心 {fc:g} kHz，带宽 {bw} kHz，"
      f"AGC={app.var_rsp_agc.get()}")
chk("中心改为 21964（避开频道）", abs(fc - 21964) <= 1, f"{fc:g}")
_want_bw = P.recommend_bw_khz(dict(P.band_channels())["21.9 MHz"], fc)
chk("带宽改为推荐值 %d kHz" % _want_bw, bw == _want_bw, str(bw))
chk("AGC 设为 50 Hz", app.var_rsp_agc.get() == "50 Hz", app.var_rsp_agc.get())
chk("优化后不再报 DC 重合", "DC 尖峰" not in after)
cfg2 = app.cfg
chk("已回写 cfg", cfg2.rsp_freq_khz == fc and cfg2.rsp_bw_khz == bw
    and cfg2.rsp_agc == 2, f"freq={cfg2.rsp_freq_khz} bw={cfg2.rsp_bw_khz}")

print("\n  优化后面板提示：\n    " + after.replace("\n", "\n    "))
print("\n结果:", "PASS" if ok else "FAIL")
root.destroy()
sys.exit(0 if ok else 1)
