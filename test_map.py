# -*- coding: utf-8 -*-
"""验证地图：鼠标拖拽平移、等比投影（不变形）、滚轮缩放保持比例。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tkinter as tk
from worldmap import WorldMap

BASE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(BASE, "world_ne110m.geojson")


class Ev:
    """伪造 matplotlib 事件。"""

    def __init__(self, ax, x=None, y=None, button=1, xdata=None, ydata=None):
        self.inaxes = ax
        self.x = x
        self.y = y
        self.button = button
        self.xdata = xdata
        self.ydata = ydata


def main():
    root = tk.Tk()
    root.geometry("1100x700")
    m = WorldMap(root, GEO)
    root.update()

    ok = True

    # ---- 1) 等比例投影 ----
    asp = m.ax.get_aspect()
    good = (asp == 1.0)
    print(f"① 纵横比 get_aspect() = {asp}  -> {'OK (等比例)' if good else 'FAIL'}")
    ok &= good

    # ---- 2) 拖拽平移（先放大，世界全景时全图已在视野内、无处置平移）----
    for _ in range(4):                       # 放大到可平移的层级
        m._on_scroll(Ev(m.ax, button="up", xdata=0.0, ydata=0.0))
    root.update()
    x0, x1 = m._xlim
    y0, y1 = m._ylim
    b = m.ax.bbox
    px, py = b.x0 + b.width * 0.5, b.y0 + b.height * 0.5
    m._on_press(Ev(m.ax, x=px, y=py, xdata=(x0 + x1) / 2, ydata=(y0 + y1) / 2))
    # 注意 matplotlib 显示坐标 y 向上为正：右拖 = x+，上拖 = y+
    m._on_move(Ev(m.ax, x=px + 200, y=py + 100))
    root.update()
    mx0, mx1 = m._xlim
    my0, my1 = m._ylim
    moved_x = mx1 - x1
    moved_y = my1 - y1
    good = (abs(moved_x) > 0.5) and (abs(moved_y) > 0.5)
    print(f"② 拖拽: 经度变化 {moved_x:+.2f}°, 纬度变化 {moved_y:+.2f}° "
          f"-> {'OK (可平移)' if good else 'FAIL (无位移!)'}")
    ok &= good

    # 抓取方向：向右拖 -> 视窗西移(经度减小)，地图内容跟随光标右移
    good = moved_x < 0 and moved_y < 0
    print(f"   方向: 右拖→经度{'减小(内容右移)✓' if moved_x < 0 else '增大 ✗'}, "
          f"上拖→纬度{'减小(内容上移)✓' if moved_y < 0 else '增大 ✗'} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good
    m._on_release(Ev(m.ax))

    # ---- 3) 拖拽后比例仍为 1:1（不变形）----
    spanx = m._xlim[1] - m._xlim[0]
    spany = m._ylim[1] - m._ylim[0]
    bb = m.ax.bbox
    disp_ratio = bb.width / max(bb.height, 1e-9)
    data_ratio = spanx / max(spany, 1e-9)
    err = abs(disp_ratio - data_ratio) / data_ratio
    good = err < 0.02
    print(f"③ 显示比例 {disp_ratio:.4f} vs 数据比例 {data_ratio:.4f} "
          f"(偏差 {err*100:.2f}%) -> {'OK (不变形)' if good else 'FAIL'}")
    ok &= good

    # ---- 4) 滚轮缩放保持比例 ----
    before = data_ratio
    m._on_scroll(Ev(m.ax, button="up", xdata=0.0, ydata=0.0))
    root.update()
    after = (m._xlim[1] - m._xlim[0]) / max(m._ylim[1] - m._ylim[0], 1e-9)
    good = abs(after - before) / before < 0.02
    print(f"④ 缩放前后跨度比 {before:.4f} -> {after:.4f} "
          f"-> {'OK (等比缩放)' if good else 'FAIL'}")
    ok &= good

    # ---- 5) 世界全景复位 ----
    m.fit_world()
    root.update()
    good = (abs(m._xlim[0] + 180) < 1 and abs(m._xlim[1] - 180) < 1)
    print(f"⑤ fit_world: xlim={tuple(round(v,1) for v in m._xlim)} "
          f"-> {'OK' if good else 'FAIL'}")
    ok &= good

    root.destroy()
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
