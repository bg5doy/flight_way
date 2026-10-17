# -*- coding: utf-8 -*-
"""选中高亮 / 地图点击取消 / 航班号显示模式 测试。

注意：Tkinter 不是线程安全的 —— 所有 Tk 操作必须在主线程执行。
      因此本测试用 root.after 步进驱动，而不是在后台线程里直接碰控件
      （否则会与 _tick 的 tree.insert 并发，偶发 TclError）。
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

import matplotlib.collections as mcoll
import matplotlib.patches as mpatches
import matplotlib.text as mtext
import tkinter as tk

from main import HFDLApp

SEL_FACE = (1.0, 0.82, 0.30, 1.0)
R = {}

root = tk.Tk()
root.geometry("1500x900")
app = HFDLApp(root)
root.after(300, app.start_sim)


class MEv:
    def __init__(self, ax, x=None, y=None, button=1):
        self.inaxes = ax
        self.x = x
        self.y = y
        self.button = button
        self.xdata = 0.0
        self.ydata = 0.0


class FF:
    def __init__(self, k, lat, lon):
        self.flight, self.reg = k, "B-1234"
        self.lat, self.lon = lat, lon
        self.heading, self.altitude, self.speed = 90.0, None, None

    def key(self):
        return self.flight

    def position_ok(self):
        return True


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------
def ring_of():
    for a in app.map._dyn:
        if isinstance(a, mpatches.Circle):
            ec = a.get_edgecolor()
            if a.get_linestyle() not in ("-", "solid") and len(ec) >= 3 \
                    and ec[0] > 0.9 and ec[1] > 0.7:
                return a
    return None


def sel_poly():
    for a in app.map._dyn:
        if isinstance(a, mpatches.Polygon):
            fc = a.get_facecolor()
            if len(fc) >= 3 and abs(fc[0] - SEL_FACE[0]) < 0.02 \
                    and abs(fc[1] - SEL_FACE[1]) < 0.02:
                return a
    return None


def has_bold_label():
    return any(isinstance(a, mtext.Text) and a.get_fontweight() == "bold"
               for a in app.map._dyn)


def has_thick_track():
    return any(isinstance(a, mcoll.LineCollection) and a.get_linewidths()[0] >= 2.0
               for a in app.map._dyn)


STEPS = []


def step():
    if not STEPS:
        root.after(150, root.destroy)
        return
    fn = STEPS.pop(0)
    try:
        fn()
    except Exception as e:
        R["error"] = repr(e)
        STEPS.clear()
    root.after(120, step)


def wait_flights(n=2, tries=40):
    """返回一个等待步骤（递归重排自身直到飞机出现）。"""
    def _w():
        if app.map.counts() >= n:
            return
        _w.tries -= 1
        if _w.tries > 0:
            STEPS.insert(0, _w)
    _w.tries = tries
    return _w


# ----------------------------------------------------------------------
# 测试步骤（全部在 Tk 主线程执行）
# ----------------------------------------------------------------------
def s_check_initial():
    R["flights"] = app.map.counts()
    app.map.set_selected(None)
    app.map.refresh()
    R["no_ring_when_none"] = ring_of() is None
    R["no_selpoly_when_none"] = sel_poly() is None


def s_select():
    key = list(app.map.flights.keys())[0]
    R["key"] = key
    st = app.map.flights[key]
    now = time.time()
    st.track = [(now - 600, st.lat - 0.5, st.lon - 0.5), (now, st.lat, st.lon)]
    app.tree.selection_set(key)
    app._on_tree_select()
    app.map.refresh()
    R["sel_is_key"] = (app.map.selected_key == key)
    R["ring"] = ring_of() is not None
    R["sel_poly"] = sel_poly() is not None
    R["bold_label"] = has_bold_label()
    R["thick_track"] = has_thick_track()
    R["tree_sel"] = list(app.tree.selection())
    sizes = sorted((float(a.get_xy()[:, 0].max() - a.get_xy()[:, 0].min())
                    for a in app.map._dyn if isinstance(a, mpatches.Polygon)),
                   reverse=True)
    R["size_ratio"] = (sizes[0] / sizes[1]) if len(sizes) >= 2 and sizes[1] > 0 else 0


def s_refresh_keeps():
    key = R["key"]
    keep = True
    for _ in range(3):
        app._refresh_tree()
        if list(app.tree.selection()) != [key] or app.map.selected_key != key:
            keep = False
            break
    R["selection_kept"] = keep
    R["row_tagged"] = ("sel" in app.tree.item(key, "tags")) \
        if key in app.tree.get_children() else None


def s_double_click():
    st = app.map.flights[R["key"]]
    app._on_tree_activate()
    a0, a1 = app.map._xlim
    R["centered"] = (abs((a0 + a1) / 2 - st.lon) < 0.5) and (a1 - a0 <= 20.5)
    R["span"] = round(a1 - a0, 1)


def s_click_clears():
    key = R["key"]
    app.tree.selection_set(key)
    app._on_tree_select()
    b = app.map.ax.bbox
    px, py = b.x0 + b.width / 2, b.y0 + b.height / 2
    app.map._on_press(MEv(app.map.ax, px, py))
    app.map._on_release(MEv(app.map.ax, px, py))          # 原地松开 = 单击
    R["click_clears"] = (app.map.selected_key is None and not app.tree.selection())


def s_drag_keeps():
    key = R["key"]
    app.tree.selection_set(key)
    app._on_tree_select()
    b = app.map.ax.bbox
    px, py = b.x0 + b.width / 2, b.y0 + b.height / 2
    x0 = app.map._xlim[0]
    app.map._on_press(MEv(app.map.ax, px, py))
    app.map._on_move(MEv(app.map.ax, px + 60, py + 30))   # 明显拖动
    app.map._on_release(MEv(app.map.ax, px + 60, py + 30))
    R["drag_keeps"] = (app.map.selected_key == key)
    R["drag_panned"] = abs(app.map._xlim[0] - x0) > 0.1


def s_fid_modes():
    """_label_text 三种模式各自正确（直接设地图模式，属单元级检查）。"""
    st2 = app.map.upsert(FF("CCA981", 30.0, 100.0))
    st3 = app.map.upsert(FF("AAL100", 32.0, 102.0))
    ok = True
    for mode, e2, e3 in (("raw", "CCA981", "AAL100"),
                         ("iata", "CA981", "AA100"),
                         ("both", "CA981 (CCA981)", "AA100 (AAL100)")):
        app.map.set_flight_id_mode(mode)
        g2 = app.map._label_text(st2).split("\n")[0]
        g3 = app.map._label_text(st3).split("\n")[0]
        R[f"label_{mode}"] = (g2, g3)
        ok &= (g2, g3) == (e2, e3)
    R["label_ok"] = ok
    # 还原地图模式，避免污染后续步骤（真实程序里地图模式始终跟随 _fid_mode）
    app.map.set_flight_id_mode(app._fid_mode)


def s_tree_fid_mode():
    """航班号显示：默认必须是 IATA 码，且切换时地图与列表同时立即生效。

    回归点：
      · 早先 _preview_fid_mode 只改地图、列表仍读 cfg → 两处不一致
      · 默认模式曾是 raw，界面上看到的还是 CCA981 这种报文原文
    """
    R["default_mode"] = app.cfg.flight_id_mode

    st2 = app.map.upsert(FF("CCA981", 30.0, 100.0))   # ICAO 呼号，应显示 CA981
    st3 = app.map.upsert(FF("MU5104", 32.0, 102.0))   # IATA 4 位，应原样保留
    app._tree_keys = []
    app._refresh_tree()

    # —— 默认（iata）：报文里的 ICAO 呼号要显示成 IATA 码 ——
    R["tree_default"] = (app.tree.item("CCA981", "values") or [None])[0]
    R["map_default"] = app.map._label_text(st2).split("\n")[0]
    R["default_is_iata"] = (R["tree_default"] == R["map_default"] == "CA981")
    R["four_digit_kept"] = (app.map._label_text(st3).split("\n")[0] == "MU5104")

    cfg_before = app.cfg.flight_id_mode

    # —— 切到「两者都显示」：地图与列表必须同时变，且 cfg 不落盘 ——
    app.var_fid_mode.set("两者都显示")
    app._preview_fid_mode()
    v = app.tree.item("CCA981", "values")
    R["tree_both"] = v[0] if v else None
    R["map_both"] = app.map._label_text(st2).split("\n")[0]
    R["fid_sync_both"] = (R["tree_both"] == R["map_both"] == "CA981 (CCA981)")
    R["cfg_not_persisted"] = (app.cfg.flight_id_mode == cfg_before)

    # —— 切到「原始 ID」 ——
    app.var_fid_mode.set("HFDL 原始 ID")
    app._preview_fid_mode()
    v = app.tree.item("CCA981", "values")
    R["tree_raw"] = v[0] if v else None
    R["map_raw"] = app.map._label_text(st2).split("\n")[0]
    R["fid_sync_raw"] = (R["tree_raw"] == R["map_raw"] == "CCA981")

    # —— 切回「商业航班号」 ——
    app.var_fid_mode.set("商业航班号 (IATA)")
    app._preview_fid_mode()
    v = app.tree.item("CCA981", "values")
    R["tree_iata"] = v[0] if v else None
    R["map_iata"] = app.map._label_text(st2).split("\n")[0]
    R["fid_sync_iata"] = (R["tree_iata"] == R["map_iata"] == "CA981")


def s_clear():
    app.tree.selection_remove(*app.tree.selection())
    app._on_tree_select()
    app.map.refresh()
    R["cleared"] = (app.map.selected_key is None and ring_of() is None)


def s_clear_on_prune():
    key = R["key"]
    app.map.set_selected(key)
    app.map.flights[key].last_seen = time.time() - 99999
    app.map.set_stale_minutes(1)
    dead = app.map.prune()
    if app.map.selected_key and app.map.selected_key in dead:
        app.map.set_selected(None)
    R["cleared_on_prune"] = app.map.selected_key is None


STEPS += [wait_flights(2), s_check_initial, s_select, s_refresh_keeps,
          s_double_click, s_click_clears, s_drag_keeps, s_fid_modes,
          s_tree_fid_mode, s_clear, s_clear_on_prune]

root.after(600, step)
root.mainloop()

for k in ("flights", "no_ring_when_none", "no_selpoly_when_none", "sel_is_key", "ring",
          "sel_poly", "bold_label", "thick_track", "tree_sel", "size_ratio",
          "selection_kept", "row_tagged", "centered", "span", "click_clears",
          "drag_keeps", "drag_panned", "label_ok",
          "default_mode", "default_is_iata", "four_digit_kept",
          "tree_default", "map_default", "tree_iata", "map_iata", "fid_sync_iata",
          "cfg_not_persisted", "tree_raw", "map_raw", "fid_sync_raw",
          "tree_both", "map_both", "fid_sync_both",
          "cleared", "cleared_on_prune", "error"):
    print(f"  {k:<22} {R.get(k)}")

ok = (R.get("error") is None
      and R.get("no_ring_when_none") and R.get("no_selpoly_when_none")
      and R.get("sel_is_key") and R.get("ring") and R.get("sel_poly")
      and R.get("bold_label") and R.get("thick_track")
      and (R.get("size_ratio") or 0) > 1.2
      and R.get("selection_kept") and R.get("row_tagged")
      and R.get("centered") and R.get("click_clears") and R.get("drag_keeps")
      and R.get("drag_panned") and R.get("label_ok")
      # 默认必须转成 IATA 码显示；4 位号码原样保留
      and R.get("default_mode") == "iata"
      and R.get("default_is_iata") and R.get("four_digit_kept")
      # 切换时列表与地图同步，且未点「应用参数」不落盘
      and R.get("fid_sync_iata") and R.get("fid_sync_raw")
      and R.get("fid_sync_both") and R.get("cfg_not_persisted")
      and R.get("cleared") and R.get("cleared_on_prune"))
print("\n结果:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
