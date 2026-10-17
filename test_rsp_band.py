# -*- coding: utf-8 -*-
"""RSP 波段自动匹配测试：验证「频道跟着波段走」的修复：中心频率改到任意 HFDL 波段都能正确匹配频道。"""
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
from main import HFDLApp

root = tk.Tk()
app = HFDLApp(root)
root.update()
ok = True


def chk(name, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {'OK  ' if good else 'FAIL'} {name}{('  ' + detail) if detail else ''}")


print("=" * 70)
print("场景复现：用户把中心频率设成 21928 kHz（21.9 MHz 段）")
print("=" * 70)
# 默认预设是 8.9 MHz 段，直接过滤必然落空 —— 这正是原来的 bug
preset = list(app.cfg.effective_freqs())
print(f"  当前频道预设: {len(preset)} 个，首尾 {preset[0]:g}…{preset[-1]:g} kHz")

chans, src, band = app._rsp_pick_channels(21928.0, 1000.0)
chk("21928 kHz 能找到频道（修复点）", bool(chans),
    f"{len(chans)} 个，来源={src}")
chk("匹配到 21.9 MHz 段", band == "21.9 MHz", str(band))
chk("包含 21928 本身", 21928 in chans)
print(f"    频道: {' '.join('%g' % c for c in chans)}")

app.var_rsp_freq.set("21928")
app._update_rsp_hint()
hint = app.lbl_rsp.cget("text")
print("\n  面板提示：\n    " + hint.replace("\n", "\n    "))
chk("提示不再说「没有频道」", "没有 HFDL 频道" not in hint)
chk("提示列出将监听的频道", "将监听" in hint)
chk("21928 与频道重合 → 有 DC 警告", "DC 尖峰" in hint)

print("\n" + "=" * 70)
print("各波段逐一验证（含推荐中心频率）")
print("=" * 70)
for name, ch in P.band_channels():
    rec = P.recommend_center_khz(ch)
    got, src2, band2 = app._rsp_pick_channels(rec, 1000.0)
    good = (set(ch) <= set(got) and band2 == name)   # 窗口会覆盖相邻波段，只要求本段频道全在内
    chk(f"{name:<10} 中心 {rec:5d} → {len(got):2d} 个（本段 {len(ch):2d} 个全含）",
        good, "" if good else f"来源={src2}")

print("\n" + "=" * 70)
print("无频道时的建议")
print("=" * 70)
sb, sc = app._suggest_center(20800.0)
print(f"  中心 20800 kHz（两波段之间）→ 建议 {sb} 段 {sc} kHz")
chk("给出合理建议", bool(sb) and bool(sc))

app.var_rsp_freq.set("8903")
app._update_rsp_hint()

print("\n结果:", "PASS" if ok else "FAIL")
root.destroy()
sys.exit(0 if ok else 1)
