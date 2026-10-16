# -*- coding: utf-8 -*-
"""地图显示测试：航向推算、民航机图标（多部件）、缩放优化、标签避让、航迹。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "TkAgg")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import tkinter as tk
import matplotlib.collections as mcoll
import matplotlib.patches as mpatches
import matplotlib.text as mtext

import worldmap as wm
from worldmap import (WorldMap, FlightState, AIRCRAFT_OUTLINE, AIRCRAFT_PARTS,
                      TRACK_MAX_MIN, initial_bearing, haversine_km)

BASE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(BASE, "world_ne110m.geojson")


class FakeFrame:
    """模拟解析出的报文对象。heading=None 表示报文不含航向（HFDL 实际情况）。"""

    def __init__(self, key, lat, lon, hdg=None, alt=None, spd=None):
        self._key = key
        self.flight = key
        self.reg = "B-0000"
        self.lat, self.lon = lat, lon
        self.heading, self.altitude, self.speed = hdg, alt, spd

    def key(self):
        return self._key

    def position_ok(self):
        return self.lat is not None and self.lon is not None


def main():
    root = tk.Tk()
    root.geometry("1200x760")
    m = WorldMap(root, GEO)
    root.update()
    ok = True

    # ================= 航向推算（核心修复）=================
    print("— 航向推算（HFDL 报文不含航向，由航迹推算）—")

    # ---- A) 大圆方位角函数 ----
    cases = [((10.0, 0.0, 11.0, 0.0), 0, "正北"),
             ((10.0, 0.0, 10.0, 1.0), 90, "正东"),
             ((11.0, 0.0, 10.0, 0.0), 180, "正南"),
             ((10.0, 1.0, 10.0, 0.0), 270, "正西")]
    good = True
    for (la1, lo1, la2, lo2), want, name in cases:
        got = initial_bearing(la1, lo1, la2, lo2)
        d = abs((got - want + 180) % 360 - 180)
        good &= d < 0.6
        print(f"   {name}: 算得 {got:6.1f}° (期望 {want}°) -> {'OK' if d < 0.6 else 'FAIL'}")
    print(f"A) 大圆方位角 -> {'OK' if good else 'FAIL'}")
    ok &= good

    # ---- B) 距离函数 ----
    d1 = haversine_km(0, 0, 0, 1)          # 赤道上 1° ≈ 111.19 km
    d2 = haversine_km(0, 0, 1, 0)          # 1° 纬度 ≈ 111.19 km
    good = abs(d1 - 111.19) < 0.5 and abs(d2 - 111.19) < 0.5
    print(f"B) 距离: 赤道1°={d1:.2f}km 纬度1°={d2:.2f}km -> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good

    # ---- C) 由航迹推算航向 ----
    good = True
    for lat1, lon1, lat2, lon2, want, name in [
            (10.0, 0.0, 11.0, 0.0, 0, "北"),
            (10.0, 0.0, 10.0, 1.0, 90, "东"),
            (11.0, 0.0, 10.0, 0.0, 180, "南"),
            (10.0, 1.0, 10.0, 0.0, 270, "西")]:
        fs = FlightState("T")
        fs.update(FakeFrame("T", lat1, lon1))       # 第 1 点：航向仍未知
        first_unknown = fs.heading is None
        fs.update(FakeFrame("T", lat2, lon2))       # 第 2 点：应推算出航向
        d = abs((fs.heading - want + 180) % 360 - 180) if fs.heading is not None else 999
        hg = d < 1.0 and fs.heading_src == "track" and first_unknown
        good &= hg
        print(f"   向{name}飞: 第1点航向={'(未知)' if first_unknown else 'ERR'} "
              f"第2点={fs.heading if fs.heading is None else f'{fs.heading:.1f}°'} "
              f"来源={fs.heading_src} -> {'OK' if hg else 'FAIL'}")
    print(f"C) 航迹推算航向 -> {'OK' if good else 'FAIL'}")
    ok &= good

    # ---- D) 位移过小不更新航向（避免抖动）----
    fs = FlightState("T")
    fs.update(FakeFrame("T", 10.0, 0.0))
    fs.update(FakeFrame("T", 10.0, 1.0))          # 向东 ~109km
    h_before = fs.heading
    for _ in range(5):
        fs.update(FakeFrame("T", 10.0, 1.000001))  # 几乎不动（<2km）
    h_after = fs.heading
    good = (h_before is not None and abs(h_after - h_before) < 25.0)
    print(f"D) 静止/微动: 移动前 {h_before:.1f}° -> 微动 5 次后 {h_after:.1f}° "
          f"-> {'OK（不抖动）' if good else 'FAIL'}")
    ok &= good

    # ---- E) 航向平滑：来回抖动应被抑制 ----
    fs = FlightState("T")
    fs.update(FakeFrame("T", 10.0, 0.0))
    for i in range(10):
        fs.update(FakeFrame("T", 10.0 + i * 0.5, 0.6))   # 大致北偏东
    h1 = fs.heading
    for i in range(10):
        fs.update(FakeFrame("T", 15.0 + i * 0.5, 0.6 + (0.05 if i % 2 else -0.05)))
    h2 = fs.heading
    good = h1 is not None and h2 is not None and abs((h2 - h1 + 180) % 360 - 180) < 45
    print(f"E) 平滑: 10 点后 {h1:.1f}°，再 10 点（含抖动）后 {h2:.1f}° "
          f"-> {'OK（未剧烈摆动）' if good else 'FAIL'}")
    ok &= good

    # ---- F) 报文若给出航向则优先采用 ----
    fs = FlightState("T")
    fs.update(FakeFrame("T", 10.0, 0.0, hdg=123.0))
    good = (abs(fs.heading - 123.0) < 0.01 and fs.heading_src == "msg")
    print(f"F) 报文航向优先: heading={fs.heading:.1f}° 来源={fs.heading_src} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    # ================= 图标 =================
    print("\n— 民航客机图标 —")
    body = AIRCRAFT_PARTS[0]
    good = (len(AIRCRAFT_PARTS) == 3 and len(body) >= 16)
    print(f"G) 部件数: {len(AIRCRAFT_PARTS)}（机体+左右发动机）-> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good

    # 对称性与形心
    cx, cy = wm._centroid(AIRCRAFT_OUTLINE)
    asym = 0.0
    for px, py in AIRCRAFT_OUTLINE:
        asym = max(asym, float(np.min(np.hypot(
            AIRCRAFT_OUTLINE[:, 0] + px, AIRCRAFT_OUTLINE[:, 1] - py))))
    good = abs(cx) < 1e-9 and abs(cy) < 1e-9 and asym < 1e-9
    print(f"H) 机体对称: 形心=({cx:+.1e},{cy:+.1e}) 对称误差={asym:.1e} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    # 钝机头：机头处应为一段圆滑肩部，而非单点尖角
    nose_y = AIRCRAFT_OUTLINE[:, 1].max()
    near = AIRCRAFT_OUTLINE[AIRCRAFT_OUTLINE[:, 1] > nose_y - 0.06]
    good = (len(near) >= 2 and np.ptp(near[:, 0]) > 0.05)
    print(f"I) 机头钝圆: 顶部 0.06 内 {len(near)} 个点，横向跨度 "
          f"{np.ptp(near[:, 0]):.3f} -> {'OK（非尖角）' if good else 'FAIL'}")
    ok &= good

    # 发动机短舱落在机翼范围内
    eng = AIRCRAFT_PARTS[1]
    ex = float(eng[:, 0].mean())
    good = (0.25 < abs(ex) < 0.60)
    print(f"J) 发动机位置: 短舱中心 x={ex:+.3f}（应在翼展中段）-> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good

    # 航向指向
    nose_i = int(np.argmax(AIRCRAFT_OUTLINE[:, 1]))
    allgood = True
    for hdg, name, pred in [(0, "北", lambda x, y: y > 0.5 and abs(x) < 0.3),
                            (90, "东", lambda x, y: x > 0.5 and abs(y) < 0.3),
                            (180, "南", lambda x, y: y < -0.5 and abs(x) < 0.3),
                            (270, "西", lambda x, y: x < -0.5 and abs(y) < 0.3),
                            (135, "东南", lambda x, y: x > 0.3 and y < -0.3)]:
        poly = m._plane_poly(0.0, 0.0, hdg, 1.0, 1.0)
        x, y = poly[nose_i]
        g = pred(x, y)
        allgood &= g
        if not g:
            print(f"   航向 {hdg}° 机头({x:+.2f},{y:+.2f}) 应为{name} -> FAIL")
    print(f"K) 机头指向（含东南）-> {'OK' if allgood else 'FAIL'}")
    ok &= allgood

    # 缩放优化
    sizes = []
    for span in (360.0, 120.0, 40.0, 12.0, 3.0):
        m._xlim = (-span / 2, span / 2)
        m._ylim = (-span / 2, span / 2)
        m._apply()
        root.update()
        sizes.append(m.icon_size_px())
    good = all(8 <= s <= 40 for s in sizes) and sizes == sorted(sizes) and sizes[0] < sizes[-1]
    print(f"L) 缩放优化: {sizes[0]:.1f}px(全球) -> {sizes[-1]:.1f}px(放大) -> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good

    # ================= 航向未知时的显示 =================
    print("\n— 航向未知的处理 —")
    m.flights.clear()
    m.set_show_labels(False)
    m.set_show_tracks(False)
    f = FakeFrame("NOHDG", 40.0, 116.0)
    st = m.upsert(f)          # 只有 1 个定位点 -> 航向未知
    m.fit_world()
    m.refresh()
    root.update()
    n_poly = len([a for a in m._dyn if isinstance(a, mpatches.Polygon)])
    n_circ = len([a for a in m._dyn if isinstance(a, mpatches.Circle)])
    good = (st.heading is None and n_poly == 0 and n_circ >= 1)
    print(f"M) 航向未知: heading={st.heading} 飞机多边形={n_poly} 圆环={n_circ} "
          f"-> {'OK（画圆环而非朝北的飞机）' if good else 'FAIL'}")
    ok &= good

    # 给出第 2 个定位点后应变成飞机并指向正确方向
    m.upsert(FakeFrame("NOHDG", 41.0, 116.0))     # 向北飞
    m.refresh()
    root.update()
    n_poly2 = len([a for a in m._dyn if isinstance(a, mpatches.Polygon)])
    good = (st.heading is not None and abs(st.heading) < 1.0 and n_poly2 == 3)
    print(f"N) 两点后: heading={st.heading:.1f}° 多边形={n_poly2}"
          f"（机体+2发动机）-> {'OK' if good else 'FAIL'}")
    ok &= good

    # ================= 航迹 =================
    print("\n— 航迹 —")
    m.set_track_minutes(99999)
    good = (m.track_min == TRACK_MAX_MIN == 960)
    print(f"O) 16h 上限: 99999 分 -> {m.track_min} 分 -> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good
    m.set_track_minutes(120)

    m.flights.clear()
    m.show_tracks = True
    st = m.upsert(FakeFrame("TRK", 40.0, 170.0))
    now = __import__("time").time()
    st.track = [(now - 3000, 30.0, 150.0), (now - 2000, 35.0, 170.0),
                (now - 1000, 40.0, -175.0), (now, 42.0, -160.0)]
    m.fit_world()
    m.refresh()
    root.update()
    lcs = [a for a in m._dyn if isinstance(a, mcoll.LineCollection)]
    nseg = sum(len(lc.get_segments()) for lc in lcs)
    good = (nseg == 2)
    print(f"P) 跨日界线: 4 点 -> {nseg} 段（应为 2）-> {'OK' if good else 'FAIL'}")
    ok &= good

    m.set_show_tracks(False)
    m.refresh()
    root.update()
    good = len([a for a in m._dyn if isinstance(a, mcoll.LineCollection)]) == 0
    print(f"Q) 航迹开关: 关闭后无航迹 -> {'OK' if good else 'FAIL'}")
    ok &= good
    m.set_show_tracks(True)

    # 起点圆点半径应为像素级（此前误算成 2.3° 大圆）
    m.flights.clear()
    m.upsert(FakeFrame("DOT", 40.0, 0.0))
    st = m.flights["DOT"]
    st.track = [(now - 600, 39.0, -1.0), (now, 40.0, 0.0)]
    m._xlim, m._ylim = (-20.0, 20.0), (-10.0, 30.0)
    m._apply()
    m.refresh()
    root.update()
    circs = [a for a in m._dyn if isinstance(a, mpatches.Circle)]
    rads = [c.get_radius() for c in circs] or [0]
    spanx = m._xlim[1] - m._xlim[0]
    good = all(r < spanx * 0.01 for r in rads)
    print(f"R) 起点圆点半径: {rads[0]:.4f}° (跨度 {spanx:.0f}°，应 <1%) -> "
          f"{'OK' if good else 'FAIL'}")
    ok &= good

    # ================= 标签避让 =================
    print("\n— 标签 —")
    m.flights.clear()
    for i in range(40):
        m.upsert(FakeFrame(f"FL{i:03d}", 40.0 + i * 0.01, 116.0 + i * 0.01, alt=35000))
    m.set_show_labels(True)
    m.fit_world()
    m.refresh()
    root.update()
    ntxt = len([a for a in m._dyn if isinstance(a, mtext.Text)])
    good = (0 < ntxt < 40)
    print(f"S) 避让: 40 架重叠 -> 标注 {ntxt} 个 -> {'OK' if good else 'FAIL'}")
    ok &= good

    m.set_show_labels("auto")
    m.refresh()
    root.update()
    good = len([a for a in m._dyn if isinstance(a, mtext.Text)]) == 0
    print(f"T) auto（40 架全球视图）-> 标注 0 -> {'OK' if good else 'FAIL'}")
    ok &= good

    root.destroy()
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
