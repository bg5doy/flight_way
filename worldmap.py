# -*- coding: utf-8 -*-
"""
全球矢量地图（matplotlib，嵌入 Tk）。
  · 加载 Natural Earth 110m 国家多边形（矢量，可无限缩放）
  · 滚轮多级缩放（以光标为中心），左键拖拽平移，等比例投影（不变形）
  · 飞机：**民航客机俯视图标**（机身+机翼+翼梢小翼+平尾+两台发动机短舱），
    按航向旋转；尺寸以屏幕像素计（不随缩放/纬度拉伸），随缩放适度放大
  · 航向来源：报文若带航向则直接用；HFDL 报文通常**不含航向**，
    因此由**航迹相邻定位点推算真方位角**（大圆初始方位），并在单位圆上做
    指数平滑，避免抖动。航向未知时画中空圆环，避免误认为"朝北"
  · 航迹：按时间渐变淡出，跨 180° 经线自动分段，最多保留 16 小时
  · 超过 stale_minutes 未更新的飞机自动从地图擦除
"""
import json
import math
import os
import time

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.patches as mpatches
from matplotlib.collections import LineCollection

from flight_id import display_flight_id

# 航迹最多保留 16 小时（界面可调，但不超过此值）
TRACK_MAX_MIN = 16 * 60

# 航向推算参数
R_EARTH_KM = 6371.0
HDG_MIN_MOVE_KM = 2.0      # 两定位点相距小于此值不更新航向（避免静止抖动）
HDG_ALPHA = 0.35           # 单位圆上指数平滑系数（越小越平滑）
HDG_LOOKBACK = 12          # 最多向前回溯多少个航迹点找"足够远"的参考点

# ----------------------------------------------------------------------
# 民航客机俯视轮廓（归一化坐标：机头 +y=1，翼尖 |x|≈1，机尾 -y=1）
#   机体 = 机身 + 机翼（含翼梢小翼）+ 平尾；发动机短舱单独成块。
#   只写右半边，再镜像成完整外形。
# ----------------------------------------------------------------------
_BODY_HALF = [
    (0.000,  1.000),   # 机头（钝圆，民航机特征）
    (0.048,  0.976),
    (0.078,  0.910),
    (0.096,  0.760),
    (0.104,  0.120),   # 机身最宽处 / 翼根前缘
    (0.560, -0.148),   # 翼中段前缘
    (0.930, -0.300),   # 翼尖前缘
    (1.000, -0.360),   # 翼梢小翼 外缘
    (0.982, -0.462),   # 翼梢小翼 后缘
    (0.892, -0.418),   # 翼尖后缘
    (0.300, -0.500),   # 翼根后缘
    (0.148, -0.690),
    (0.455, -0.858),   # 平尾 前缘
    (0.430, -0.946),   # 平尾 后缘
    (0.105, -0.912),
    (0.074, -0.982),
    (0.000, -1.000),   # 机尾（须在 x=0 上，保证左右对称）
]

# 发动机短舱中心（左右各一），长轴沿机身方向
_ENGINE_CENTERS = [(0.372, -0.072), (-0.372, -0.072)]
_ENGINE_HX, _ENGINE_HY = 0.043, 0.084


def _centroid(pts):
    """多边形面积形心。"""
    x, y = pts[:, 0], pts[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y2 - x2 * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-12:
        return tuple(pts.mean(axis=0))
    return (((x + x2) * cross).sum() / (6.0 * a),
            ((y + y2) * cross).sum() / (6.0 * a))


def _pod(hx, hy, n=14):
    """椭圆形发动机短舱轮廓（中心在原点）。"""
    t = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    return np.column_stack([hx * np.cos(t), hy * np.sin(t)])


def _build_parts():
    half = np.array(_BODY_HALF, dtype=float)
    left = np.array([(-x, y) for (x, y) in reversed(_BODY_HALF[1:-1])], dtype=float)
    body = np.vstack([half, left])
    cx, cy = _centroid(body)
    body = body - np.array([cx, cy])
    parts = [body]
    for ex, ey in _ENGINE_CENTERS:
        parts.append(_pod(_ENGINE_HX, _ENGINE_HY) + np.array([ex - cx, ey - cy]))
    return body, parts


# AIRCRAFT_OUTLINE：机体外形（含机翼/平尾），供外部引用与测试
# AIRCRAFT_PARTS  ：全部绘制块 [机体, 左发, 右发]
AIRCRAFT_OUTLINE, AIRCRAFT_PARTS = _build_parts()


# ----------------------------------------------------------------------
# 航向推算
# ----------------------------------------------------------------------
def initial_bearing(lat1, lon1, lat2, lon2):
    """大圆初始方位角（真方位，0=正北，顺时针）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return math.degrees(math.atan2(y, x)) % 360.0


def haversine_km(lat1, lon1, lat2, lon2):
    """两点大圆距离（km）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * R_EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


class FlightState:
    __slots__ = ("key", "flight", "reg", "lat", "lon", "heading", "heading_src",
                 "altitude", "speed", "last_seen", "first_seen", "track", "count",
                 "_hcos", "_hsin")

    def __init__(self, key):
        self.key = key
        self.flight = key
        self.reg = ""
        self.lat = None
        self.lon = None
        self.heading = None
        self.heading_src = None      # "msg"=报文给出 / "track"=航迹推算
        self.altitude = None
        self.speed = None
        self.last_seen = time.time()
        self.first_seen = time.time()
        self.track = []              # [(ts, lat, lon), ...] 按时间升序
        self.count = 0
        self._hcos = None            # 航向平滑（单位圆上的向量和）
        self._hsin = None

    def prune_track(self, window_sec):
        cut = time.time() - window_sec
        if self.track and self.track[0][0] < cut:
            # 至少保留最新一点，避免整条航迹消失
            self.track = [p for p in self.track if p[0] >= cut] or self.track[-1:]

    def _derive_heading(self):
        """由最近两个相距足够远的定位点推算航向。

        HFDL 性能报文只含 lat/lon（见 OpenHFDL src/hfnpdu.c:37），不含航向，
        所以必须由航迹推算。若位移太小则保留上一次航向，避免抖动。
        """
        if len(self.track) < 2:
            return False
        _, la2, lo2 = self.track[-1]
        ref = self.track[:-1]
        for _, la1, lo1 in reversed(ref[-HDG_LOOKBACK:]):
            if haversine_km(la1, lo1, la2, lo2) < HDG_MIN_MOVE_KM:
                continue
            b = math.radians(initial_bearing(la1, lo1, la2, lo2))
            s, c = math.sin(b), math.cos(b)
            if self._hcos is None:
                self._hcos, self._hsin = c, s
            else:                     # 单位圆上指数平滑
                k = HDG_ALPHA
                self._hcos = (1.0 - k) * self._hcos + k * c
                self._hsin = (1.0 - k) * self._hsin + k * s
            self.heading = math.degrees(
                math.atan2(self._hsin, self._hcos)) % 360.0
            self.heading_src = "track"
            return True
        return False

    def update(self, fr, track_minutes=120):
        self.flight = fr.flight or self.flight
        self.reg = fr.reg or self.reg
        if fr.altitude is not None:
            self.altitude = fr.altitude
        if fr.speed is not None:
            self.speed = fr.speed
        self.last_seen = time.time()
        self.count += 1
        # 报文若直接给出航向（有些数据源会有），优先采用
        if fr.heading is not None:
            self.heading = float(fr.heading) % 360.0
            self.heading_src = "msg"
            r = math.radians(self.heading)
            self._hcos, self._hsin = math.cos(r), math.sin(r)
        if fr.position_ok():
            self.lat, self.lon = fr.lat, fr.lon
            self.prune_track(max(1, int(track_minutes)) * 60)
            self.track.append((self.last_seen, fr.lat, fr.lon))
            # 16 小时按 20 秒一帧最多约 2880 点，留足余量
            if len(self.track) > 6000:
                self.track = self.track[-6000:]
            if fr.heading is None:
                self._derive_heading()


class WorldMap:
    MIN_SPAN = 0.8
    MAX_SPAN = 360.0

    def __init__(self, parent, geojson_path, on_click=None):
        self.parent = parent
        self.on_click = on_click       # 在地图上单击（非拖拽）时回调
        self.flights = {}
        self.stale_sec = 3600
        self.track_min = 120            # 航迹保留分钟数（≤ TRACK_MAX_MIN）
        self.show_tracks = True
        self.show_labels = "auto"       # True / False / "auto"
        self.icon_base_px = 15.0        # 全球视图下的图标像素尺寸
        self.selected_key = None        # 列表中被选中的航班（高亮显示）
        self.flight_id_mode = "iata"    # 航班号显示模式：iata（默认）/ raw / both
        self._dyn = []                  # 本帧动态 artists
        self._polys = []
        self._drag = None               # 拖拽起点（像素坐标）
        self._drag_ref = None           # 拖拽起点时的 (xlim, ylim, 画布宽, 高)
        self._drag_moved = False        # 本次按下是否发生过拖拽（用于区分单击）

        self.fig = Figure(figsize=(11, 5.6), dpi=100, facecolor="#0a1b2c")
        self.ax = self.fig.add_subplot(111)
        # 收紧留白，让地图尽量占满可用区域
        self.fig.subplots_adjust(left=0.035, right=0.995, top=0.985, bottom=0.055)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.w = self.canvas.get_tk_widget()
        self.w.pack(fill="both", expand=True)

        self._xlim = (-180.0, 180.0)
        self._ylim = (-85.0, 85.0)
        # 等比例投影：1°经度 = 1°纬度，避免地图被拉伸变形。
        self.ax.set_aspect("equal", adjustable="box", anchor="C")
        self._paths = self._load_map(geojson_path)
        self._draw_static()
        self._bind()

    # ---------------------------------------------------------------- map
    @staticmethod
    def _load_map(path):
        paths = []
        if not os.path.exists(path):
            return paths
        try:
            with open(path, "r", encoding="utf-8") as fh:
                gj = json.load(fh)
        except Exception:
            return paths
        for feat in gj.get("features", []):
            geom = feat.get("geometry") or {}
            t = geom.get("type")
            if t == "Polygon":
                rings = [geom["coordinates"][0]]
            elif t == "MultiPolygon":
                rings = [p[0] for p in geom["coordinates"]]
            else:
                continue
            for ring in rings:
                arr = np.asarray(ring, dtype=float)
                if arr.ndim == 2 and arr.shape[0] >= 3:
                    paths.append(arr[:, :2])
        return paths

    def _draw_static(self):
        for a in self._polys:
            try:
                a.remove()
            except Exception:
                pass
        self._polys = []
        for arr in self._paths:
            p = mpatches.Polygon(arr, closed=True, facecolor="#14384f",
                                 edgecolor="#3f7fa6", linewidth=0.4, zorder=1)
            self.ax.add_patch(p)
            self._polys.append(p)
        self.ax.set_facecolor("#08192a")
        self.ax.grid(True, color="#1c3f5e", lw=0.5, alpha=0.5)
        self.ax.tick_params(colors="#7fb3d5", labelsize=8)
        for s in self.ax.spines.values():
            s.set_color("#2a5f85")
        self.ax.set_xlim(self._xlim)
        self.ax.set_ylim(self._ylim)
        self.canvas.draw_idle()

    # ------------------------------------------------------------- events
    def _bind(self):
        c = self.canvas
        c.mpl_connect("scroll_event", self._on_scroll)
        c.mpl_connect("button_press_event", self._on_press)
        c.mpl_connect("button_release_event", self._on_release)
        c.mpl_connect("motion_notify_event", self._on_move)

    def _on_scroll(self, ev):
        if ev.inaxes is not self.ax or ev.xdata is None:
            return
        cx, cy = ev.xdata, ev.ydata
        f = 0.75 if ev.button == "up" else 1.0 / 0.75
        x0, x1 = self._xlim
        y0, y1 = self._ylim
        sx, sy = x1 - x0, y1 - y0
        nsx = float(np.clip(sx * f, self.MIN_SPAN, self.MAX_SPAN))
        # x/y 用同一个缩放系数，保持等比投影的跨度比不变（否则缩放会变形）
        k = (nsx / sx) if sx else 1.0
        nsy = sy * k
        rx = (cx - x0) / sx if sx else .5
        ry = (cy - y0) / sy if sy else .5
        self._xlim = (float(cx - rx * nsx), float(cx - rx * nsx + nsx))
        self._ylim = (float(cy - ry * nsy), float(cy - ry * nsy + nsy))
        self._apply()

    def _on_press(self, ev):
        """记录拖拽起点：像素坐标 + 当时的坐标范围与画布尺寸。"""
        if ev.inaxes is self.ax and ev.button == 1 and ev.x is not None:
            self._drag = (ev.x, ev.y)
            self._drag_moved = False
            self._drag_ref = (tuple(self._xlim), tuple(self._ylim),
                              max(float(self.ax.bbox.width), 1.0),
                              max(float(self.ax.bbox.height), 1.0))
            try:
                self.canvas.get_tk_widget().configure(cursor="fleur")
            except Exception:
                pass

    def _on_release(self, ev):
        # 按下到松开之间若几乎没移动，视为「单击」而不是拖拽
        was_press = self._drag_ref is not None
        moved = self._drag_moved
        self._drag = None
        self._drag_ref = None
        self._drag_moved = False
        try:
            self.canvas.get_tk_widget().configure(cursor="")
        except Exception:
            pass
        if was_press and not moved and self.on_click is not None:
            try:
                self.on_click()
            except Exception:
                pass

    def _on_move(self, ev):
        if self._drag is None or self._drag_ref is None or ev.x is None:
            return
        # 超过 4px 才算拖拽，避免手抖把单击变成平移、或把平移当成单击
        if (abs(ev.x - self._drag[0]) > 4 or abs(ev.y - self._drag[1]) > 4):
            self._drag_moved = True
        (x0, x1), (y0, y1), w, h = self._drag_ref
        # 像素位移 -> 数据位移（范围取拖拽开始时的快照，避免反馈）
        dx = (ev.x - self._drag[0]) / w * (x1 - x0)
        dy = (ev.y - self._drag[1]) / h * (y1 - y0)
        self._xlim = (float(x0 - dx), float(x1 - dx))
        self._ylim = (float(y0 - dy), float(y1 - dy))
        self._apply()

    def _apply(self):
        """应用视窗：限制纬度过冲、经度越界，并保持等比例投影。"""
        y0, y1 = self._ylim
        sy = y1 - y0
        if sy >= 180.0:                       # 跨度覆盖整个纬度 -> 显示全球
            self._ylim = (-90.0, 90.0)
        elif y0 < -90.0:                      # 不允许越过南极点
            self._ylim = (-90.0, float(-90.0 + sy))
        elif y1 > 90.0:                       # 不允许越过北极点
            self._ylim = (float(90.0 - sy), 90.0)
        x0, x1 = self._xlim
        if x1 - x0 >= 360.0:
            self._xlim = (-180.0, 180.0)
        else:                                 # 允许环绕，但视野别完全跑出图外
            self._xlim = (float(max(x0, -540.0)), float(min(x1, 540.0)))
        self.ax.set_aspect("equal", adjustable="box", anchor="C")
        self.ax.set_xlim(self._xlim)
        self.ax.set_ylim(self._ylim)
        self.canvas.draw_idle()

    def fit_world(self):
        self._xlim = (-180.0, 180.0)
        self._ylim = (-85.0, 85.0)
        self._apply()

    # ------------------------------------------------------------- flights
    def set_stale_minutes(self, m):
        self.stale_sec = max(1, int(m)) * 60

    def set_track_minutes(self, m):
        """航迹保留时长（分钟）。上限 16 小时（TRACK_MAX_MIN）。"""
        self.track_min = int(np.clip(int(m), 1, TRACK_MAX_MIN))

    def set_show_tracks(self, b):
        self.show_tracks = bool(b)

    def set_show_labels(self, mode):
        """mode: True=始终显示 / False=不显示 / "auto"=按缩放自动。"""
        self.show_labels = mode

    def set_icon_size(self, px):
        self.icon_base_px = float(np.clip(float(px), 6.0, 40.0))

    def set_selected(self, key):
        """设置高亮航班（None 表示取消高亮）。key 为 FlightState.key。"""
        self.selected_key = key

    def set_flight_id_mode(self, mode):
        """航班号显示模式：iata=转 IATA 码（默认）/ raw=报文原文 / both=两者。"""
        self.flight_id_mode = mode if mode in ("raw", "iata", "both") else "iata"

    def center_on(self, key, span=None):
        """把视野移到指定航班（可选指定经度跨度）。"""
        st = self.flights.get(key)
        if st is None or st.lat is None:
            return False
        if span is None:
            span = self._xlim[1] - self._xlim[0]
        span = float(np.clip(span, self.MIN_SPAN, self.MAX_SPAN))
        # 维持当前纵横比
        cur_x = self._xlim[1] - self._xlim[0]
        cur_y = self._ylim[1] - self._ylim[0]
        ratio = (cur_y / cur_x) if cur_x else 0.5
        sy = span * ratio
        self._xlim = (st.lon - span / 2, st.lon + span / 2)
        self._ylim = (st.lat - sy / 2, st.lat + sy / 2)
        self._apply()
        return True

    def upsert(self, fr):
        key = fr.key()
        st = self.flights.get(key)
        if st is None:
            st = FlightState(key)
            self.flights[key] = st
        st.update(fr, self.track_min)
        return st

    def prune(self):
        now = time.time()
        dead = [k for k, s in self.flights.items() if now - s.last_seen > self.stale_sec]
        for k in dead:
            self.flights.pop(k, None)
        return dead

    def counts(self):
        return len(self.flights)

    def track_point_count(self):
        return sum(len(s.track) for s in self.flights.values())

    def heading_stats(self):
        """返回 (已知航向数, 航迹推算数, 未知数)，供日志/自检。"""
        known = sum(1 for s in self.flights.values() if s.heading is not None)
        trk = sum(1 for s in self.flights.values() if s.heading_src == "track")
        return known, trk, len(self.flights) - known

    # ------------------------------------------------------------- drawing
    def icon_size_px(self):
        """图标像素尺寸：全球视图为基准，视野越小适度放大（缩放优化）。"""
        span = max(self._xlim[1] - self._xlim[0], 1e-6)
        k = float(np.clip(math.log2(360.0 / span), 0.0, 3.0))   # 0..3 档
        return float(np.clip(self.icon_base_px * (1.0 + 0.30 * k), 6.0, 40.0))

    def _rotate(self, pts, lon, lat, heading, dx, dy):
        ang = math.radians(heading)
        ca, sa = math.cos(ang), math.sin(ang)
        lx = pts[:, 0] * dx
        ly = pts[:, 1] * dy
        # 航向 0 = 正北(+y)，顺时针为正
        rx = lx * ca + ly * sa
        ry = -lx * sa + ly * ca
        return np.column_stack([lon + rx, lat + ry])

    def _plane_polys(self, lon, lat, heading, dx, dy):
        """返回按航向旋转后的全部绘制块（机体 + 两台发动机）。"""
        return [self._rotate(p, lon, lat, heading, dx, dy)
                for p in AIRCRAFT_PARTS]

    def _plane_poly(self, lon, lat, heading, dx, dy):
        """仅机体外形（供测试/兼容）。"""
        return self._rotate(AIRCRAFT_OUTLINE, lon, lat, heading, dx, dy)

    def _draw_track(self, st, now, window, sx, highlight=False):
        """航迹：按时间渐变（越旧越淡），跨 180° 经线不连线。

        highlight=True 时用更亮的琥珀色 + 更粗线宽，并压在其它航迹之上。
        """
        pts = st.track
        if len(pts) < 2:
            return
        segs, cols = [], []
        for (t1, la1, lo1), (t2, la2, lo2) in zip(pts[:-1], pts[1:]):
            if abs(lo2 - lo1) > 180.0:        # 跨日界线，断开
                continue
            segs.append([(lo1, la1), (lo2, la2)])
            age = (now - t2) / window if window > 0 else 1.0
            if highlight:
                a = float(np.clip(0.98 - 0.40 * age, 0.50, 0.98))
                cols.append((1.0, 0.80, 0.20, a))
            else:
                a = float(np.clip(0.85 - 0.70 * age, 0.10, 0.85))
                cols.append((1.0, 0.72, 0.02, a))
        if not segs:
            return
        lc = LineCollection(segs, colors=cols,
                            linewidths=(2.8 if highlight else 1.2),
                            zorder=(5 if highlight else 3), capstyle="round")
        self.ax.add_collection(lc)
        self._dyn.append(lc)
        # 起点小圆点（半径按**像素**计），标示航迹起始
        t0, la0, lo0 = pts[0]
        dot = mpatches.Circle(
            (lo0, la0), (3.6 if highlight else 2.5) * sx, facecolor="none",
            edgecolor=("#ffd166" if highlight else "#ffb703"),
            lw=(1.8 if highlight else 0.9),
            alpha=(1.0 if highlight else 0.7),
            zorder=(5 if highlight else 3))
        self.ax.add_patch(dot)
        self._dyn.append(dot)

    def _label_text(self, st):
        """标签：航班号 + （航向 / 高度，有则显示）。航班号按显示模式选择。"""
        line2 = []
        if st.heading is not None and st.heading_src == "track":
            line2.append(f"{st.heading:03.0f}°")
        if st.altitude:
            line2.append(f"FL{int(st.altitude) // 100:03d}")
        name = display_flight_id(st.flight, self.flight_id_mode)
        return name + ("\n" + " ".join(line2) if line2 else "")

    def _place_label(self, st, dx, dy, sx, sy, placed, highlight=False):
        """放置航班标签：带避让，重叠则跳过（高亮的标签优先放置且不参与避让放弃）。"""
        tag = self._label_text(st)
        lines = tag.split("\n")
        fs = 7.5 if highlight else 7.0
        w = max(len(s) for s in lines) * fs * 0.60 * sx
        h = len(lines) * fs * 1.35 * sy
        tx = st.lon + dx * 1.4
        ty = st.lat - dy * 2.0
        box = (tx - w / 2, ty - h, tx + w / 2, ty + h)
        for (bx0, by0, bx1, by1) in placed:
            if not (box[2] < bx0 or box[0] > bx1 or box[3] < by0 or box[1] > by1):
                if not highlight:
                    return False
        placed.append(box)
        if highlight:
            t = self.ax.text(tx, ty, tag, color="#fff3d0", fontsize=fs, zorder=10,
                             ha="center", va="bottom", linespacing=1.15,
                             fontweight="bold",
                             bbox=dict(boxstyle="round,pad=0.25", fc="#4a3200",
                                       ec="#ffd166", lw=1.2, alpha=0.95))
        else:
            t = self.ax.text(tx, ty, tag, color="#d8f6ff", fontsize=fs, zorder=7,
                             ha="center", va="bottom", linespacing=1.15,
                             bbox=dict(boxstyle="round,pad=0.20", fc="#062033",
                                       ec="#2f6f95", lw=0.5, alpha=0.72))
        self._dyn.append(t)
        return True

    def refresh(self):
        for a in self._dyn:
            try:
                a.remove()
            except Exception:
                pass
        self._dyn = []

        x0, x1 = self._xlim
        y0, y1 = self._ylim
        b = self.ax.bbox
        bw = max(float(b.width), 1.0)
        bh = max(float(b.height), 1.0)
        sx = (x1 - x0) / bw          # 数据单位/像素
        sy = (y1 - y0) / bh

        now = time.time()
        window = max(self.track_min, 1) * 60.0

        vis = [st for st in self.flights.values()
               if st.lat is not None
               and (x0 - 5.0 <= st.lon <= x1 + 5.0)
               and (y0 - 5.0 <= st.lat <= y1 + 5.0)]

        sel = self.selected_key
        # 选中的排在最后绘制（配合 zorder 保证压在最上层）
        ordered = [s for s in vis if s.key != sel]
        sel_st = next((s for s in vis if s.key == sel), None)
        if sel_st is not None:
            ordered.append(sel_st)

        # ---- 航迹（画在图标下面）----
        if self.show_tracks:
            for st in ordered:
                self._draw_track(st, now, window, sx, highlight=(st.key == sel))

        # ---- 标签显隐策略 ----
        mode = self.show_labels
        if mode is False:
            show_lbl = False
        elif mode is True:
            show_lbl = True
        else:                        # auto：飞机少或已放大时显示
            show_lbl = (len(vis) <= 14) or ((x1 - x0) < 60.0)

        # ---- 飞机图标 ----
        base_half = self.icon_size_px() / 2.0
        placed = []
        # 有选中项时，其余适当压暗，突出高亮目标
        dim = 0.45 if sel_st is not None else 1.0
        for st in ordered:
            is_sel = (st.key == sel)
            half = base_half * (1.45 if is_sel else 1.0)
            dx, dy = half * sx, half * sy
            age = (now - st.last_seen) / max(self.stale_sec, 1)
            fresh = float(np.clip(1.0 - age, 0.35, 1.0)) * (1.0 if is_sel else dim)
            if is_sel:
                # 高亮光环
                self._dyn.append(self._add(mpatches.Circle(
                    (st.lon, st.lat), half * 1.75 * sx, facecolor="none",
                    edgecolor="#ffd166", lw=1.4, ls=(0, (3, 2)), alpha=0.95,
                    zorder=8)))
            if st.heading is None:
                # 航向未知：中空圆环 + 圆心，明确区别于"朝北"
                ec = "#ffd166" if is_sel else "#7fe3ff"
                self._dyn.append(self._add(mpatches.Circle(
                    (st.lon, st.lat), half * sx, facecolor="none",
                    edgecolor=ec, lw=(1.6 if is_sel else 1.0), zorder=9 if is_sel else 6)))
                self._dyn.append(self._add(mpatches.Circle(
                    (st.lon, st.lat), half * sx * 0.30, facecolor=ec,
                    edgecolor="none", zorder=9 if is_sel else 6)))
            else:
                fc = ((1.0, 0.82, 0.30, 1.0) if is_sel
                      else (0.0, 0.90 * fresh, 1.0, 0.95 * dim))
                for poly in self._plane_polys(st.lon, st.lat, st.heading, dx, dy):
                    self._dyn.append(self._add(mpatches.Polygon(
                        poly, closed=True, facecolor=fc,
                        edgecolor=("#ffffff" if is_sel else "#eaffff"),
                        linewidth=(1.0 if is_sel else 0.5),
                        zorder=9 if is_sel else 6)))
            if show_lbl or is_sel:
                self._place_label(st, dx, dy, sx, sy, placed, highlight=is_sel)

        self.canvas.draw_idle()

    def _add(self, artist):
        """加入坐标轴并返回（统一登记以便下一帧清除）。"""
        if isinstance(artist, mpatches.Patch):
            self.ax.add_patch(artist)
        else:
            self.ax.add_artist(artist)
        return artist
