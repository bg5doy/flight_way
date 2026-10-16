# -*- coding: utf-8 -*-
"""
HFDL 航空数据链解码 + 全球地图航迹显示（GUI 主程序）。

解码后端：
  1. **OpenHFDL (dumphfdl)** —— 成熟解码器，推荐方式。
     · 本地：调用本机/WSL 内的 dumphfdl，直接对接 SDR（SoapySDR）或回放 I/Q 文件
     · 远程：连接远端 dumphfdl 的 TCP JSON 输出（适合把接收机放在别处）
  2. 内置教学解调（声卡音频）—— 演示用，非位级兼容实现
  3. 仿真示例 —— 无需任何硬件，合成 dumphfdl 风格 JSON 走完整解析→地图→入库链路

应用功能：
  · 全球矢量地图（Natural Earth 110m），滚轮缩放 / 拖拽平移
  · 飞机按航向绘制，同一航班显示历史航迹
  · 超过 stale_minutes（默认 60）未更新的飞机自动擦除
  · 全部报文写入 SQLite，可按时间/航班号检索

用法：python main.py
"""
import json
import math
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import (HfdlConfig, BACKEND_OPENHFDL_LOCAL, BACKEND_OPENHFDL_SOUNDCARD,
                    BACKEND_OPENHFDL_REMOTE, BACKEND_LABELS, HFDL_SYMBOL_RATE)
import audio as aud
import hfdl_presets as presets
import openhfdl_client as ohc
import soundcard_bridge as scb
import worldmap
from store import FlightStore
from worldmap import WorldMap

BASE = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(BASE, "world_ne110m.geojson")
DB_PATH = os.path.join(BASE, "hfdl_flights.db")


class HFDLApp:
    # ------------------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title("HFDL 航空数据链解码器 · OpenHFDL")
        self.root.geometry("1600x920")

        self.cfg = HfdlConfig()
        self.cfg.db_path = DB_PATH
        # 旧配置可能指向已移除的后端，回退到可用后端
        if self.cfg.backend not in BACKEND_LABELS:
            self.cfg.backend = BACKEND_OPENHFDL_LOCAL
        self.presets = presets.build_decode_presets()
        self.stations = presets.load_systable()

        self.store = FlightStore(DB_PATH)
        self.decoder = None          # OpenHfdlDecoder / RemoteJsonFeed
        self.running = threading.Event()
        self.sim_running = threading.Event()
        self.worker = None
        self.frame_q = queue.Queue()     # 解码线程 -> Tk 线程
        self.log_q = queue.Queue()

        self._build_ui()
        self._setup_map()
        self._probe_openhfdl()
        self._sync_panels()
        self._repopulate_devices()
        self._tick()

    # ==================================================================
    # UI
    # ==================================================================
    def _build_ui(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side="top", fill="x")

        self.btn_start = ttk.Button(bar, text="▶ 开始解码", command=self.start_decode)
        self.btn_start.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(bar, text="■ 停止", command=self.stop_decode,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_sim = ttk.Button(bar, text="▶ 仿真示例", command=self.start_sim)
        self.btn_sim.pack(side="left", padx=3)
        self.btn_simstop = ttk.Button(bar, text="■ 停止仿真", command=self.stop_sim,
                                      state="disabled")
        self.btn_simstop.pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="🔍 查询", command=self.open_query).pack(side="left", padx=3)
        ttk.Button(bar, text="⛶ 世界全景",
                   command=lambda: self.map.fit_world()).pack(side="left", padx=3)
        ttk.Button(bar, text="📖 系统表", command=self.show_systable).pack(side="left", padx=3)
        ttk.Button(bar, text="✕ 退出", command=self._on_close).pack(side="right", padx=4)

        self.lbl_status = ttk.Label(bar, text="就绪", foreground="#2a7")
        self.lbl_status.pack(side="right", padx=12)

        main = ttk.Frame(self.root)
        main.pack(side="top", fill="both", expand=True)
        self._build_config(main)          # 左：参数面板（可滚动）
        body = ttk.Frame(main)
        body.pack(side="left", fill="both", expand=True)
        self._build_map_panel(body)       # 上：地图（横向铺满，世界地图约 2:1）
        self._build_bottom(body)          # 下：解析日志 + 当前飞机

    # ------------------------------------------------------------------
    def _build_config(self, parent):
        """左侧参数面板。内容较多，包一层可滚动 Canvas，避免窗口偏小时被截断。"""
        wrap = ttk.Frame(parent)
        wrap.pack(side="left", fill="y", padx=(8, 4), pady=6)

        self.cfg_canvas = tk.Canvas(wrap, borderwidth=0, highlightthickness=0,
                                    width=306, bg="#f0f0f0")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.cfg_canvas.yview)
        self.cfg_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.cfg_canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(self.cfg_canvas)
        self._cfg_win = self.cfg_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda e: self.cfg_canvas.configure(
            scrollregion=self.cfg_canvas.bbox("all")))
        self.cfg_canvas.bind("<Configure>", lambda e: self.cfg_canvas.itemconfigure(
            self._cfg_win, width=e.width))
        self._bind_wheel(self.cfg_canvas)

        # 输入设备列表（声卡桥接 与 内置解调 共用）
        try:
            self.devices = aud.list_input_devices()
        except Exception:
            self.devices = []

        # ---------- 后端选择 ----------
        f0 = ttk.LabelFrame(outer, text="解码后端", padding=8)
        f0.pack(fill="x")
        self.var_backend = tk.StringVar(
            value=BACKEND_LABELS.get(self.cfg.backend, self.cfg.backend))
        cmb = ttk.Combobox(f0, textvariable=self.var_backend, width=36, state="readonly",
                           values=[BACKEND_LABELS[k] for k in
                                   (BACKEND_OPENHFDL_LOCAL, BACKEND_OPENHFDL_SOUNDCARD,
                                    BACKEND_OPENHFDL_REMOTE)])
        cmb.pack(fill="x")
        cmb.bind("<<ComboboxSelected>>", lambda e: self._on_backend_change())
        self.lbl_oh = ttk.Label(f0, text="检测中…", foreground="#a60", wraplength=255,
                                justify="left")
        self.lbl_oh.pack(fill="x", pady=(4, 0))

        # ---------- 声卡枚举（解决 Windows 按主机 API 重复枚举的问题）----------
        fe = ttk.LabelFrame(outer, text="声卡枚举", padding=8)
        fe.pack(fill="x", pady=(6, 0))
        ttk.Label(fe, text="主机 API:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_hostapi = tk.StringVar(value="自动（推荐 WASAPI）")
        self._hostapi_choices = ["自动（推荐 WASAPI）"] + \
            [n for _, n, _ in aud.list_hostapis()]
        self.cmb_hostapi = ttk.Combobox(fe, textvariable=self.var_hostapi, width=18,
                                        state="readonly", values=self._hostapi_choices)
        self.cmb_hostapi.grid(row=0, column=1, sticky="ew", pady=2)
        self.cmb_hostapi.bind("<<ComboboxSelected>>",
                              lambda e: self._repopulate_devices())
        self.var_exclusive = tk.BooleanVar(value=False)
        ttk.Checkbutton(fe, text="WASAPI 独占模式（绕过系统音效，利于解调）",
                        variable=self.var_exclusive).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=2)
        self.lbl_devcount = ttk.Label(fe, text="", foreground="#567", wraplength=250,
                                      justify="left")
        self.lbl_devcount.grid(row=2, column=0, columnspan=2, sticky="w")

        # ---------- OpenHFDL 参数（本机唯一可用输入：I/Q 文件回放）----------
        # 注：SoapySDR 直连模式在本机不可执行（SDRuno 独占 SDRplay 硬件，
        #     且 dumphfdl 跑在 WSL 内无法访问 USB 设备），故已从界面移除。
        self.frm_oh = ttk.LabelFrame(outer, text="OpenHFDL 参数（I/Q 文件回放）",
                                     padding=8)
        self.frm_oh.pack(fill="x", pady=(6, 0))
        g = self.frm_oh

        ttk.Label(g, text="I/Q 文件:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_iq = tk.StringVar(value=self.cfg.oh_iq_file)
        fr_iq = ttk.Frame(g)
        fr_iq.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Entry(fr_iq, textvariable=self.var_iq, width=13).pack(side="left", fill="x",
                                                                  expand=True)
        ttk.Button(fr_iq, text="…", width=2,
                   command=self._pick_iq).pack(side="left")

        ttk.Label(g, text="I/Q 格式:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_sfmt = tk.StringVar(value=self.cfg.oh_sample_format)
        ttk.Combobox(g, textvariable=self.var_sfmt, width=18, state="readonly",
                     values=presets.SAMPLE_FORMATS).grid(row=1, column=1, sticky="ew",
                                                         pady=2)

        ttk.Label(g, text="采样率 (Hz):").grid(row=2, column=0, sticky="w", pady=2)
        self.var_ohsr = tk.StringVar(value=str(self.cfg.oh_sample_rate))
        ttk.Combobox(g, textvariable=self.var_ohsr, width=18,
                     values=[str(x) for x in presets.SAMPLE_RATES]).grid(
            row=2, column=1, sticky="ew", pady=2)

        ttk.Label(g, text="中心频率 (kHz):").grid(row=3, column=0, sticky="w", pady=2)
        self.var_centerfreq = tk.StringVar(value=str(self.cfg.oh_centerfreq))
        ttk.Entry(g, textvariable=self.var_centerfreq, width=18).grid(
            row=3, column=1, sticky="ew", pady=2)

        ttk.Label(g, text="频道预设:").grid(row=4, column=0, sticky="w", pady=2)
        self.var_preset = tk.StringVar()
        names = [p["name"] for p in self.presets]
        self.cmb_preset = ttk.Combobox(g, textvariable=self.var_preset, width=18,
                                       state="readonly", values=names)
        self.cmb_preset.grid(row=4, column=1, sticky="ew", pady=2)
        self.cmb_preset.bind("<<ComboboxSelected>>", lambda e: self._apply_preset())

        ttk.Label(g, text="频道列表 (kHz):").grid(row=5, column=0, sticky="nw", pady=2)
        self.txt_freqs = tk.Text(g, height=4, width=22, font=("Consolas", 8))
        self.txt_freqs.grid(row=5, column=1, sticky="ew", pady=2)
        self.txt_freqs.insert("1.0", " ".join(f"{f:g}" for f in self.cfg.oh_freqs))

        ttk.Label(g, text="输出格式:").grid(row=6, column=0, sticky="w", pady=2)
        self.var_ofmt = tk.StringVar(value=self.cfg.oh_output_format)
        ttk.Combobox(g, textvariable=self.var_ofmt, width=18, state="readonly",
                     values=["json", "basestation", "text"]).grid(
            row=6, column=1, sticky="ew", pady=2)

        ttk.Label(g, text="远端 host:port:").grid(row=7, column=0, sticky="w", pady=2)
        self.var_remote = tk.StringVar(
            value=f"{self.cfg.oh_remote_host}:{self.cfg.oh_remote_port}")
        ttk.Entry(g, textvariable=self.var_remote, width=18).grid(
            row=7, column=1, sticky="ew", pady=2)

        self.var_utc = tk.BooleanVar(value=self.cfg.oh_utc)
        ttk.Checkbutton(g, text="UTC 时间戳", variable=self.var_utc).grid(
            row=8, column=0, columnspan=2, sticky="w")
        self.var_mpdu = tk.BooleanVar(value=self.cfg.oh_output_mpdus)
        ttk.Checkbutton(g, text="输出 MPDU", variable=self.var_mpdu).grid(
            row=9, column=0, columnspan=2, sticky="w")
        ttk.Button(g, text="重新检测 dumphfdl", command=self._probe_openhfdl).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.lbl_iqhint = ttk.Label(g, text="", foreground="#567", wraplength=250,
                                    justify="left")
        self.lbl_iqhint.grid(row=11, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ---------- 信号 → I/Q 桥接（供 OpenHFDL 使用声卡 / SDR IQ OUT） ----------
        self.frm_bridge = ttk.LabelFrame(
            outer, text="信号桥接（OpenHFDL 用声卡 / SDR IQ OUT）", padding=8)
        self.frm_bridge.pack(fill="x", pady=(6, 0))
        br = self.frm_bridge

        # 信号来源：声卡实音频 vs 真正的复基带 I/Q
        ttk.Label(br, text="信号来源:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_bridge_source = tk.StringVar(
            value=scb.SOURCE_LABELS.get(self.cfg.oh_bridge_source,
                                        scb.SOURCE_LABELS[scb.SOURCE_AUDIO]))
        cmb_src = ttk.Combobox(br, textvariable=self.var_bridge_source, width=18,
                               state="readonly",
                               values=[scb.SOURCE_LABELS[k] for k in scb.SOURCES])
        cmb_src.grid(row=0, column=1, sticky="ew", pady=2)
        cmb_src.bind("<<ComboboxSelected>>", lambda e: self._on_source_change())

        ttk.Label(br, text="桥接模式:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_bridge_mode = tk.StringVar(
            value=scb.MODE_LABELS.get(self.cfg.oh_bridge_mode, scb.MODE_LABELS[scb.MODE_ANALYTIC]))
        mode_labels = [scb.MODE_LABELS[m] for m in scb.MODES]
        self.cmb_bridge_mode = ttk.Combobox(br, textvariable=self.var_bridge_mode,
                                            width=18, state="readonly", values=mode_labels)
        self.cmb_bridge_mode.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(br, text="采集设备:").grid(row=2, column=0, sticky="w", pady=2)
        dev_names = [f"[{i}] {n}" for i, n, _ in self.devices] or ["（无可用设备）"]
        idx = self.cfg.oh_bridge_device
        sel = next((d for d in dev_names if d.startswith(f"[{idx}]")), dev_names[0])
        self.var_bridge_dev = tk.StringVar(value=sel)
        self.cmb_bridge_dev = ttk.Combobox(br, textvariable=self.var_bridge_dev,
                                           values=dev_names, width=18, state="readonly")
        self.cmb_bridge_dev.grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(br, text="采样率:").grid(row=3, column=0, sticky="w", pady=2)
        self.var_bridge_rate = tk.StringVar(value=str(self.cfg.oh_bridge_rate))
        ttk.Combobox(br, textvariable=self.var_bridge_rate, width=18,
                     values=["192000", "96000", "48000", "44100", "384000"]).grid(
            row=3, column=1, sticky="ew", pady=2)

        # SDR IQ OUT 专用：VFO/中心频率
        ttk.Label(br, text="VFO 频率 (kHz):").grid(row=4, column=0, sticky="w", pady=2)
        self.var_iq_vfo = tk.StringVar(value=str(self.cfg.oh_iq_vfo_khz))
        self.ent_iq_vfo = ttk.Entry(br, textvariable=self.var_iq_vfo, width=18)
        self.ent_iq_vfo.grid(row=4, column=1, sticky="ew", pady=2)

        ttk.Label(br, text="音频中心 (Hz):").grid(row=5, column=0, sticky="w", pady=2)
        self.var_bridge_center = tk.StringVar(
            value=str(int(self.cfg.oh_bridge_audio_center)))
        ttk.Entry(br, textvariable=self.var_bridge_center, width=18).grid(
            row=5, column=1, sticky="ew", pady=2)

        # 带内自动搜索（等价 PC-HFDL/KG-HFDL 的行为；仅 audio 源需要）
        self.var_bridge_scan = tk.BooleanVar(value=bool(self.cfg.oh_bridge_scan))
        self.chk_scan = ttk.Checkbutton(br, text="自动搜索音频带内信号（推荐）",
                                        variable=self.var_bridge_scan,
                                        command=self._update_bridge_hint)
        self.chk_scan.grid(row=6, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(br, text="搜索范围 (Hz):").grid(row=7, column=0, sticky="w", pady=2)
        fr_scan = ttk.Frame(br)
        fr_scan.grid(row=7, column=1, sticky="ew", pady=2)
        self.var_scan_from = tk.StringVar(value=str(int(self.cfg.oh_bridge_scan_from)))
        self.var_scan_to = tk.StringVar(value=str(int(self.cfg.oh_bridge_scan_to)))
        ttk.Entry(fr_scan, textvariable=self.var_scan_from, width=7).pack(side="left")
        ttk.Label(fr_scan, text="–").pack(side="left", padx=2)
        ttk.Entry(fr_scan, textvariable=self.var_scan_to, width=7).pack(side="left")
        ttk.Label(fr_scan, text="Hz").pack(side="left", padx=2)

        self.lbl_bridge = ttk.Label(br, text="", foreground="#567", wraplength=260,
                                    justify="left")
        self.lbl_bridge.grid(row=8, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.cmb_bridge_mode.bind("<<ComboboxSelected>>",
                                  lambda e: self._update_bridge_hint())
        self._on_source_change()

        # ---------- 应用参数 ----------
        fa = ttk.LabelFrame(outer, text="应用 / 地图显示", padding=8)
        fa.pack(fill="x", pady=(6, 0))
        ttk.Label(fa, text="过期擦除 (分):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_stale = tk.StringVar(value=str(self.cfg.stale_minutes))
        ttk.Spinbox(fa, textvariable=self.var_stale, from_=1, to=1440, increment=10,
                    width=8).grid(row=0, column=1, sticky="ew", pady=2)

        # 航迹保留：上限 16 小时
        ttk.Label(fa, text="航迹保留 (分):").grid(row=1, column=0, sticky="w", pady=2)
        self.var_track = tk.StringVar(value=str(self.cfg.track_minutes))
        ttk.Spinbox(fa, textvariable=self.var_track, from_=5,
                    to=worldmap.TRACK_MAX_MIN, increment=30,
                    width=8).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(fa, text=f"（最多 {worldmap.TRACK_MAX_MIN} 分 = 16 小时）",
                  foreground="#567").grid(row=2, column=0, columnspan=2, sticky="w")

        self.var_show_track = tk.BooleanVar(value=bool(self.cfg.track_show))
        ttk.Checkbutton(fa, text="在地图上显示历史航迹",
                        variable=self.var_show_track).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 2))

        ttk.Label(fa, text="航班标签:").grid(row=4, column=0, sticky="w", pady=2)
        self._label_choices = {"按缩放自动": "auto", "始终显示": "always",
                               "不显示": "never"}
        cur = {v: k for k, v in self._label_choices.items()}.get(
            self.cfg.label_mode, "按缩放自动")
        self.var_label_mode = tk.StringVar(value=cur)
        ttk.Combobox(fa, textvariable=self.var_label_mode, width=10, state="readonly",
                     values=list(self._label_choices)).grid(
            row=4, column=1, sticky="ew", pady=2)

        ttk.Label(fa, text="图标大小 (px):").grid(row=5, column=0, sticky="w", pady=2)
        self.var_icon = tk.StringVar(value=str(int(self.cfg.icon_px)))
        ttk.Spinbox(fa, textvariable=self.var_icon, from_=6, to=40, increment=2,
                    width=8).grid(row=5, column=1, sticky="ew", pady=2)

        ttk.Button(fa, text="应用参数", command=self.apply_cfg).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    # ------------------------------------------------------------------
    def _bind_wheel(self, canvas):
        """仅当鼠标位于该 Canvas 上时响应滚轮，避免与地图的滚轮缩放冲突。"""
        def wheel(ev):
            canvas.yview_scroll(int(-ev.delta / 120), "units")
            return "break"

        def bind_tree(w):
            w.bind("<MouseWheel>", wheel)
            for c in w.winfo_children():
                bind_tree(c)

        bind_tree(canvas)

    def _build_map_panel(self, parent):
        frm = ttk.LabelFrame(
            parent,
            text="全球航迹图（滚轮缩放 / 左键拖拽平移 / 工具栏「世界全景」复位）",
            padding=4)
        frm.pack(side="top", fill="both", expand=True, padx=4, pady=(6, 2))
        self.map_host = tk.Frame(frm)
        self.map_host.pack(fill="both", expand=True)

    def _setup_map(self):
        self.map = WorldMap(self.map_host, GEOJSON)
        self._apply_map_prefs()

    # ------------------------------------------------------------------
    def _build_bottom(self, parent):
        """地图下方：解析日志（左） + 当前飞机（右）。横向展开，便于阅读。"""
        frm = ttk.Frame(parent)
        frm.pack(side="bottom", fill="x", padx=4, pady=(2, 6))

        logf = ttk.LabelFrame(frm, text="解析日志", padding=4)
        logf.pack(side="left", fill="both", expand=True)
        self.txt_log = tk.Text(logf, height=9, width=72, bg="#0b1a26", fg="#cfe8ff",
                               font=("Consolas", 9), state="disabled", wrap="none")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.txt_log.yview)
        sb.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=sb.set)

        acf = ttk.LabelFrame(frm, text="当前飞机", padding=4)
        acf.pack(side="left", fill="both", expand=True, padx=(4, 0))
        cols = ("flight", "reg", "pos", "alt", "hdg", "seen")
        self.tree = ttk.Treeview(acf, columns=cols, show="headings", height=9)
        for c, w, t in [("flight", 70, "航班"), ("reg", 70, "注册"), ("pos", 150, "位置"),
                        ("alt", 55, "高度"), ("hdg", 50, "航向"), ("seen", 65, "秒前")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        tsb = ttk.Scrollbar(acf, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)

    # ==================================================================
    # 辅助
    # ==================================================================
    def log(self, msg):
        self.log_q.put(msg)

    def _flush_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
        except queue.Empty:
            pass

    def _probe_openhfdl(self):
        kind, path = ohc.find_dumphfdl(self.cfg.oh_binary)
        if kind:
            self.lbl_oh.config(text=f"✓ 已找到 dumphfdl（{kind}）\n{path}",
                               foreground="#2a7")
            self.log(f"[openhfdl] 找到解码器 ({kind}): {path}")
        else:
            self.lbl_oh.config(
                text="✗ 未找到 dumphfdl。请先构建（见 README 的 OpenHFDL 章节），"
                     "或改用「仿真示例」演示。", foreground="#c33")
            self.log("[openhfdl] 未找到 dumphfdl —— 可用仿真示例演示界面")

    def _repopulate_devices(self):
        """按所选主机 API 重新枚举输入设备（解决 Windows 重复枚举）。

        「自动」= 每台物理设备只保留一个最优条目（优先 WASAPI），并过滤掉
        Microsoft Sound Mapper / 主声音捕获驱动程序 这类假设备。
        """
        choice = self.var_hostapi.get()
        filt = None if choice.startswith("自动") else choice
        try:
            self.devices = aud.list_input_devices(hostapi_name_filter=filt)
        except Exception as e:
            self.devices = []
            self.log(f"[audio] 枚举设备失败: {e}")
        names = [f"[{i}] {n}" for i, n, _ in self.devices] or ["（无可用设备）"]
        for cmb in (getattr(self, "cmb_bridge_dev", None),
                    getattr(self, "cmb_dev", None)):
            if cmb is not None:
                cmb.configure(values=names)
                if cmb.get() not in names:
                    cmb.set(names[0])
        raw = aud.enumerate_input_devices()
        self.lbl_devcount.config(
            text=f"原始枚举 {len(raw)} 条 → 去重后 {len(self.devices)} 条"
                 f"（同一设备在各主机 API 下重复出现已合并）")
        self.log(f"[audio] 主机 API={choice}，设备 {len(names)} 个：{names}")

    def _on_backend_change(self):
        label = self.var_backend.get()
        for k, v in BACKEND_LABELS.items():
            if v == label:
                self.cfg.backend = k
        self._sync_panels()
        self.log(f"[cfg] 解码后端切换为: {label}")

    def _set_panel_state(self, frame, enabled):
        """递归启用/禁用某个参数面板（ttk 控件用 state，tk.Text 用 config）。"""
        def walk(w):
            for c in w.winfo_children():
                try:
                    if isinstance(c, (ttk.Entry, ttk.Button, ttk.Combobox,
                                      ttk.Checkbutton, ttk.Spinbox)):
                        c.state(["!disabled"] if enabled else ["disabled"])
                    elif isinstance(c, tk.Text):
                        c.configure(state="normal" if enabled else "disabled")
                except Exception:
                    pass
                walk(c)
        walk(frame)

    def _sync_panels(self):
        """按后端高亮/灰化对应面板，避免选错参数区。"""
        bk = self.cfg.backend
        uses_oh = bk in (BACKEND_OPENHFDL_LOCAL, BACKEND_OPENHFDL_REMOTE)
        self._set_panel_state(self.frm_oh, uses_oh)
        self._set_panel_state(self.frm_bridge, bk == BACKEND_OPENHFDL_SOUNDCARD)
        # 面板标题加标记，指明当前生效项
        marks = {
            id(self.frm_oh): uses_oh,
            id(self.frm_bridge): bk == BACKEND_OPENHFDL_SOUNDCARD,
        }
        for frm, base in ((self.frm_oh, "OpenHFDL 参数（I/Q 文件回放）"),
                          (self.frm_bridge,
                           "信号桥接（OpenHFDL 用声卡 / SDR IQ OUT）")):
            active = marks.get(id(frm), False)
            frm.configure(text=("▶ " + base) if active else base)

    def _pick_iq(self):
        p = filedialog.askopenfilename(
            title="选择 I/Q 录制文件（SDRuno 录制的 .wav 也可）",
            filetypes=[("I/Q 录制", "*.wav *.cs16 *.cf32 *.cu8 *.iq *.bin"),
                       ("全部文件", "*.*")])
        if not p:
            return
        self.var_iq.set(p)
        self._autodetect_iq(p)

    def _autodetect_iq(self, path):
        """从 WAV 头自动识别采样率/格式/中心频率，省去手填。

        SDRuno 录制的 I/Q WAV 命名形如 SDRuno_20261014_092203Z_21697kHz.wav，
        文件名末段即为中心频率(kHz)。
        """
        import re
        info = []
        try:
            if path.lower().endswith(".wav"):
                a = scb.read_wav_any(path)
                arr, rate, ch, fmt = a
                self.var_ohsr.set(str(rate))
                self.var_sfmt.set("CS16" if "16" in fmt and ch == 2 else
                                  ("CF32" if "FLOAT32" in fmt else "CS16"))
                info.append(f"{ch}ch {fmt} {rate} Hz {len(arr)/rate:.1f}s")
        except Exception as e:
            info.append(f"（WAV 解析失败：{e}）")
        m = re.search(r"_(\d+(?:\.\d+)?)kHz", os.path.basename(path))
        if m:
            cf = float(m.group(1))
            self.var_centerfreq.set(f"{cf:g}")
            info.append(f"中心频率 {cf:g} kHz（取自文件名）")
        if info:
            self.log("[i/q] 自动识别: " + "；".join(info))
        try:
            self.lbl_iqhint.config(text="\n".join(info) if info else "")
        except Exception:
            pass

    def _on_source_change(self):
        """切换信号来源：SDR I/Q 时强制 stereo-iq 并启用 VFO 输入，禁用带内搜索。"""
        label = self.var_bridge_source.get()
        src = scb.SOURCE_AUDIO
        for k, v in scb.SOURCE_LABELS.items():
            if v == label:
                src = k
        self.cfg.oh_bridge_source = src
        is_iq = (src == scb.SOURCE_SDR_IQ)
        if is_iq:
            self.var_bridge_mode.set(scb.MODE_LABELS[scb.MODE_STEREO_IQ])
        try:
            self.cmb_bridge_mode.configure(state="disabled" if is_iq else "readonly")
            self.ent_iq_vfo.configure(state="normal" if is_iq else "disabled")
            self.chk_scan.configure(state="disabled" if is_iq else "normal")
        except Exception:
            pass
        self._update_bridge_hint()

    def _update_bridge_hint(self):
        """桥接提示：按来源给出正确的频率约定。"""
        label = self.var_bridge_source.get()
        src = scb.SOURCE_AUDIO
        for k, v in scb.SOURCE_LABELS.items():
            if v == label:
                src = k
        try:
            rate = int(float(self.var_bridge_rate.get()))
        except Exception:
            rate = self.cfg.oh_bridge_rate
        warn = ""
        if rate < scb.MIN_SAMPLE_RATE:
            warn = f"\n⚠ 采样率低于 dumphfdl 下限 {scb.MIN_SAMPLE_RATE} sps"
        if src == scb.SOURCE_SDR_IQ:
            try:
                vfo = float(self.var_iq_vfo.get())
            except Exception:
                vfo = self.cfg.oh_iq_vfo_khz
            n = len(self.cfg.effective_freqs())
            self.lbl_bridge.config(
                text=f"SDRuno: RX Control → IQ OUT 模式；WFM 子模式可取 192 kHz。\n"
                     f"需把 SDRuno 音频输出指向虚拟声卡(VB-CABLE)，本程序采集其 Output。\n"
                     f"命令：dumphfdl --centerfreq {vfo:g} <{n} 个 HFDL 射频频道>"
                     f"（1440Hz 偏置由 dumphfdl 自理）{warn}")
        else:
            try:
                c = float(self.var_bridge_center.get())
            except Exception:
                c = self.cfg.oh_bridge_audio_center
            if self.var_bridge_scan.get():
                txt = (f"自动搜索 {self.var_scan_from.get()}–{self.var_scan_to.get()} Hz")
            else:
                txt = f"固定音频中心 {c:.0f} Hz"
            self.lbl_bridge.config(
                text=f"命令：dumphfdl --centerfreq 1.44（音频频率直填频道）\n"
                     f"{txt}{warn}")

    def _apply_preset(self):
        name = self.var_preset.get()
        for p in self.presets:
            if p["name"] == name:
                self.txt_freqs.delete("1.0", "end")
                self.txt_freqs.insert("1.0", " ".join(f"{f:g}" for f in p["freqs"]))
                self.var_ohsr.set(str(p["sample_rate"]))
                self.log(f"[preset] {name}: {len(p['freqs'])} 频道, "
                         f"推荐采样率 {p['sample_rate']} Hz")
                break

    def show_systable(self):
        win = tk.Toplevel(self.root)
        win.title("HFDL 地面站系统表 (OpenHFDL systable.conf)")
        win.geometry("780x560")
        cols = ("id", "name", "lat", "lon", "freqs")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for c, w, t in [("id", 45, "ID"), ("name", 190, "地面站"),
                        ("lat", 80, "纬度"), ("lon", 80, "经度"),
                        ("freqs", 350, "频率 (kHz)")]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True)
        for st in sorted(self.stations, key=lambda s: s["id"]):
            tree.insert("", "end", values=(
                st["id"], st["name"], f"{st['lat']:.4f}", f"{st['lon']:.4f}",
                " ".join(f"{f:g}" for f in st["freqs"])))
        ttk.Label(win, text=f"共 {len(self.stations)} 个地面站；符号率固定 "
                            f"{HFDL_SYMBOL_RATE} baud；300/600bps=BPSK, "
                            f"1200bps=QPSK, 1800bps=8PSK").pack(side="bottom", pady=4)

    # ==================================================================
    # 配置收集
    # ==================================================================
    def _read_cfg(self):
        try:
            label = self.var_backend.get()
            for k, v in BACKEND_LABELS.items():
                if v == label:
                    self.cfg.backend = k
            self.cfg.oh_iq_file = self.var_iq.get().strip()
            self.cfg.oh_sample_format = self.var_sfmt.get()
            self.cfg.oh_centerfreq = float(self.var_centerfreq.get() or 0)
            self.cfg.oh_output_format = self.var_ofmt.get()
            self.cfg.oh_utc = bool(self.var_utc.get())
            self.cfg.oh_output_mpdus = bool(self.var_mpdu.get())
            self.cfg.oh_freqs_text = self.txt_freqs.get("1.0", "end").strip()
            self.cfg.oh_freqs = self.cfg.effective_freqs()
            hp = self.var_remote.get().strip()
            if ":" in hp:
                h, p = hp.rsplit(":", 1)
                self.cfg.oh_remote_host = h.strip()
                self.cfg.oh_remote_port = int(p)
            self.cfg.oh_sample_rate = int(float(self.var_ohsr.get()))
            # 声卡桥接参数
            for m, lab in scb.MODE_LABELS.items():
                if lab == self.var_bridge_mode.get():
                    self.cfg.oh_bridge_mode = m
            self.cfg.oh_bridge_rate = int(float(self.var_bridge_rate.get()))
            self.cfg.oh_bridge_audio_center = float(self.var_bridge_center.get())
            self.cfg.oh_bridge_scan = bool(self.var_bridge_scan.get())
            self.cfg.oh_bridge_scan_from = float(self.var_scan_from.get())
            self.cfg.oh_bridge_scan_to = float(self.var_scan_to.get())
            for k, v in scb.SOURCE_LABELS.items():
                if v == self.var_bridge_source.get():
                    self.cfg.oh_bridge_source = k
            self.cfg.oh_iq_vfo_khz = float(self.var_iq_vfo.get())
            bd = self.var_bridge_dev.get()
            self.cfg.oh_bridge_device = int(bd[1:].split("]")[0]) if bd.startswith("[") else -1
            self.cfg.stale_minutes = int(float(self.var_stale.get()))
            self.cfg.track_minutes = int(float(self.var_track.get()))
            self.cfg.track_show = bool(self.var_show_track.get())
            self.cfg.label_mode = self._label_choices.get(
                self.var_label_mode.get(), "auto")
            self.cfg.icon_px = float(self.var_icon.get())
            return True
        except Exception as e:
            messagebox.showerror("参数错误", f"参数解析失败：{e}")
            return False

    def apply_cfg(self):
        if not self._read_cfg():
            return
        self.map.set_stale_minutes(self.cfg.stale_minutes)
        self.map.set_track_minutes(self.cfg.track_minutes)
        self.map.set_show_tracks(self.cfg.track_show)
        self.map.set_icon_size(self.cfg.icon_px)
        self.map.set_show_labels({"auto": "auto", "always": True,
                                  "never": False}.get(self.cfg.label_mode, "auto"))
        self.map.refresh()
        self.log(f"[cfg] 已应用：后端={BACKEND_LABELS.get(self.cfg.backend, self.cfg.backend)} "
                 f"频道={len(self.cfg.oh_freqs)} 过期={self.cfg.stale_minutes}min "
                 f"航迹={self.cfg.track_minutes}min({self.cfg.track_minutes/60:.1f}h)"
                 f"{'' if self.cfg.track_show else ' 已隐藏'} "
                 f"标签={self.cfg.label_mode} 图标={self.cfg.icon_px:g}px")

    def _apply_map_prefs(self):
        """窗口启动时把配置里的地图显示偏好同步到地图对象。"""
        self.map.set_stale_minutes(self.cfg.stale_minutes)
        self.map.set_track_minutes(self.cfg.track_minutes)
        self.map.set_show_tracks(self.cfg.track_show)
        self.map.set_icon_size(self.cfg.icon_px)
        self.map.set_show_labels({"auto": "auto", "always": True,
                                  "never": False}.get(self.cfg.label_mode, "auto"))

    # ==================================================================
    # 回调（解码线程 -> 队列 -> Tk 线程）
    # ==================================================================
    def _on_frame_threadsafe(self, fr):
        self.frame_q.put(fr)

    def _drain_frames(self):
        n = 0
        while n < 300:
            try:
                fr = self.frame_q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                self.store.add(fr)
            except Exception as e:
                self.log(f"[store] 错误: {e}")
            if fr.position_ok():
                self.map.upsert(fr)
            self.log(self._frame_line(fr))

    def _frame_line(self, fr):
        ts = datetime.fromtimestamp(fr.ts).strftime("%H:%M:%S")
        pos = f"{fr.lat:.3f},{fr.lon:.3f}" if fr.position_ok() else "—"
        alt = f"FL{fr.altitude//100:03d}" if fr.altitude else "—"
        hdg = f"{fr.heading:.0f}°" if fr.heading is not None else "—"
        spd = f"{fr.speed}kt" if fr.speed else "—"
        return (f"[{ts}] {fr.flight or '?'}/{fr.reg or '?'} 位置={pos} "
                f"高度={alt} 航向={hdg} 速度={spd} | {fr.text[:70]}")

    # ==================================================================
    # 启动 / 停止解码
    # ==================================================================
    def start_decode(self):
        if self.running.is_set():
            return
        if not self._read_cfg():
            return
        bk = self.cfg.backend

        if bk == BACKEND_OPENHFDL_LOCAL:
            self.decoder = ohc.OpenHfdlDecoder(self.cfg,
                                               on_frame=self._on_frame_threadsafe,
                                               on_log=self.log)
            if not self.decoder.start():
                messagebox.showwarning(
                    "未找到 OpenHFDL",
                    "未找到 dumphfdl 可执行文件。\n\n"
                    "· 可在 WSL/Linux 中运行 wsl_build.sh 构建（见 README）\n"
                    "· 或改选「OpenHFDL 远程解码」连接远端实例\n"
                    "· 或点「仿真示例」直接演示界面")
                return
        elif bk == BACKEND_OPENHFDL_SOUNDCARD:
            kind, path = ohc.find_dumphfdl(self.cfg.oh_binary)
            if not kind:
                messagebox.showwarning(
                    "未找到 OpenHFDL",
                    "声卡桥接需要 dumphfdl。请先运行 build_openhfdl_wsl.sh 构建，\n"
                    "或在 OpenHFDL 面板中手动指定二进制路径。")
                return
            self.decoder = scb.BridgedDecoder(
                self.cfg, on_frame=self._on_frame_threadsafe, on_log=self.log,
                mode=self.cfg.oh_bridge_mode,
                audio_center_hz=self.cfg.oh_bridge_audio_center,
                soundcard_rate=self.cfg.oh_bridge_rate,
                device_index=self.cfg.oh_bridge_device,
                binary=path if kind == "wsl" else path, kind=kind,
                scan=self.cfg.oh_bridge_scan,
                scan_from=self.cfg.oh_bridge_scan_from,
                scan_to=self.cfg.oh_bridge_scan_to,
                scan_step=self.cfg.oh_bridge_scan_step,
                exclusive=bool(self.var_exclusive.get()),
                source=self.cfg.oh_bridge_source,
                vfo_khz=self.cfg.oh_iq_vfo_khz,
                rf_freqs_khz=self.cfg.effective_freqs())
            if not self.decoder.start():
                messagebox.showerror(
                    "桥接失败",
                    "无法启动声卡桥接。请检查：\n"
                    "· dumphfdl 是否可用\n"
                    "· 声卡设备是否被占用\n"
                    f"· 采样率是否 ≥ {scb.MIN_SAMPLE_RATE} sps")
                self.decoder = None
                return
        elif bk == BACKEND_OPENHFDL_REMOTE:
            self.decoder = ohc.RemoteJsonFeed(
                self.cfg.oh_remote_host, self.cfg.oh_remote_port,
                on_frame=self._on_frame_threadsafe, on_log=self.log,
                fmt="json" if self.cfg.oh_output_format == "json" else "basestation")
            if not self.decoder.start():
                messagebox.showerror("连接失败",
                                     f"无法连接 {self.cfg.oh_remote_host}:"
                                     f"{self.cfg.oh_remote_port}")
                return

        self.running.set()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_status.config(text="解码中…", foreground="#a60")
        self.log(f"[decode] 已启动（{BACKEND_LABELS.get(bk, bk)}）")

    def stop_decode(self):
        was = self.running.is_set()
        self.running.clear()
        if self.decoder:
            try:
                self.decoder.stop()
            except Exception:
                pass
            self.decoder = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="已停止", foreground="#2a7")
        if was:
            self.log("[decode] 已停止")

    # ==================================================================
    # 仿真：合成 dumphfdl 风格 JSON，走真实解析链路
    # ==================================================================
    def _sim_record(self, pl):
        """构造与 dumphfdl JSON 输出结构一致的记录。"""
        gm = time.gmtime()
        return json.dumps({
            "app": {"name": "dumphfdl", "ver": "1.7.0"},
            "t": {"sec": int(time.time()), "usec": random.randint(0, 999999)},
            "freq": random.choice([8825.0, 8927.0, 10081.0, 11384.0, 13312.0, 17919.0]),
            "bit_rate": random.choice([300, 600, 1200, 1800]),
            "sig_level": round(random.uniform(-60, -35), 1),
            "noise_level": round(random.uniform(-75, -60), 1),
            "freq_skew": round(random.uniform(-20, 20), 1),
            "slot": random.choice(["S", "D"]),
            "station": "SIM",
            "hfnpdu": {
                "type": {"id": 13, "name": "performance data"},
                "err": False,
                "flight_id": pl["flight"],
                "pos": {"lat": pl["lat"], "lon": pl["lon"]},
                "time": {"hour": gm.tm_hour, "min": gm.tm_min, "sec": gm.tm_sec},
                "alt": pl["fl"] * 100,
                "gs": pl["spd"],
                "track": pl["hdg"],
            },
        }, ensure_ascii=False)

    def start_sim(self):
        if self.sim_running.is_set():
            return
        self.sim_running.set()
        self.btn_sim.config(state="disabled")
        self.btn_simstop.config(state="normal")
        self.lbl_status.config(text="仿真中…", foreground="#a60")
        self.log("[sim] 仿真开始：合成 dumphfdl JSON → 解析 → 地图/入库")
        self.worker = threading.Thread(target=self._sim_loop, daemon=True)
        self.worker.start()

    def _sim_loop(self):
        rng = random.Random(20240501)
        planes = [
            dict(flight="MU510", reg="B2416", lat=31.2, lon=121.4, hdg=275.0, fl=350, spd=480),
            dict(flight="CA981", reg="B2032", lat=40.0, lon=116.5, hdg=90.0, fl=370, spd=500),
            dict(flight="CZ345", reg="B3062", lat=23.1, lon=113.3, hdg=210.0, fl=330, spd=460),
            dict(flight="LH400", reg="DABYA", lat=48.0, lon=8.0, hdg=250.0, fl=390, spd=520),
            dict(flight="QF12", reg="VHOQI", lat=-33.9, lon=151.2, hdg=310.0, fl=380, spd=510),
            dict(flight="AAL100", reg="N789AN", lat=40.6, lon=-73.8, hdg=75.0, fl=360, spd=495),
            dict(flight="SIA322", reg="9VSKA", lat=1.35, lon=103.99, hdg=330.0, fl=400, spd=530),
        ]
        i = 0
        while self.sim_running.is_set():
            pl = planes[i % len(planes)]
            d = pl["spd"] * 0.000011 * 0.22
            pl["lat"] += d * math.cos(math.radians(pl["hdg"]))
            pl["lon"] += d * math.sin(math.radians(pl["hdg"])) / max(
                0.3, math.cos(math.radians(pl["lat"])))
            pl["lat"] = max(-85.0, min(85.0, pl["lat"]))
            if pl["lon"] > 180:
                pl["lon"] -= 360
            if pl["lon"] < -180:
                pl["lon"] += 360
            pl["hdg"] = (pl["hdg"] + rng.uniform(-1.5, 1.5)) % 360

            line = self._sim_record(pl)
            rec = ohc.parse_openhfdl_json(line)
            fr = ohc.record_to_frame(rec, line) if rec else None
            if fr is not None:
                self.frame_q.put(fr)
            i += 1
            time.sleep(0.6)

    def stop_sim(self):
        self.sim_running.clear()
        self.btn_sim.config(state="normal")
        self.btn_simstop.config(state="disabled")
        self.lbl_status.config(text="仿真已停止", foreground="#2a7")
        self.log("[sim] 仿真停止")

    # ==================================================================
    # 查询
    # ==================================================================
    def open_query(self):
        win = tk.Toplevel(self.root)
        win.title("历史报文查询")
        win.geometry("1080x520")
        f = ttk.Frame(win, padding=8)
        f.pack(fill="x")
        ttk.Label(f, text="航班/注册:").grid(row=0, column=0, sticky="w")
        e_f = ttk.Entry(f, width=16)
        e_f.grid(row=0, column=1, padx=4)
        ttk.Label(f, text="起始 (YYYY-MM-DD HH:MM):").grid(row=0, column=2, sticky="w")
        e_t0 = ttk.Entry(f, width=18)
        e_t0.grid(row=0, column=3, padx=4)
        ttk.Label(f, text="结束:").grid(row=0, column=4, sticky="w")
        e_t1 = ttk.Entry(f, width=18)
        e_t1.grid(row=0, column=5, padx=4)

        cols = ("time", "flight", "reg", "lat", "lon", "hdg", "alt", "rate", "text")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=18)
        for c, w, t in [("time", 140, "时间"), ("flight", 70, "航班"), ("reg", 70, "注册"),
                        ("lat", 75, "纬度"), ("lon", 75, "经度"), ("hdg", 50, "航向"),
                        ("alt", 55, "高度"), ("rate", 55, "速率"), ("text", 280, "报文")]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True, padx=8, pady=4)

        def _parse_time(s):
            s = s.strip()
            if not s:
                return None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return time.mktime(time.strptime(s, fmt))
                except ValueError:
                    continue
            return None

        def do_search():
            for iid in tree.get_children():
                tree.delete(iid)
            rows = self.store.search(flight=e_f.get().strip() or None,
                                     t_from=_parse_time(e_t0.get()),
                                     t_to=_parse_time(e_t1.get()), limit=1000)
            for r in rows:
                rid, ts_text, fl, reg, lat, lon, hdg, alt, mode, ok, txt = r
                tree.insert("", "end", values=(
                    ts_text, fl or "", reg or "",
                    f"{lat:.3f}" if lat is not None else "",
                    f"{lon:.3f}" if lon is not None else "",
                    f"{hdg:.0f}" if hdg is not None else "",
                    f"FL{alt//100}" if alt else "",
                    f"{mode}bps" if mode else "",
                    (txt or "")[:120]))
            self.log(f"[query] 返回 {len(rows)} 条记录")

        ttk.Button(f, text="查询", command=do_search).grid(row=0, column=6, padx=8)
        do_search()

    # ==================================================================
    # 周期刷新
    # ==================================================================
    def _tick(self):
        try:
            self._drain_frames()
            self.map.prune()
            self.map.refresh()
            self._refresh_tree()
            self._flush_log()
            st = self.decoder.stats if self.decoder else None
            extra = f" · 解码 {st['frames']} 帧" if st else ""
            self.lbl_status.config(
                text=f"{self.map.counts()} 架飞机 · 入库 {self.store.count()} 条{extra}")
        except Exception:
            pass
        self.root.after(1000, self._tick)

    def _refresh_tree(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        now = time.time()
        items = []
        for st in self.map.flights.values():
            items.append((st.flight or "?",
                          st.reg or "",
                          f"{st.lat:.2f},{st.lon:.2f}" if st.lat is not None else "—",
                          f"FL{st.altitude//100}" if st.altitude else "—",
                          f"{st.heading:.0f}°" if st.heading is not None else "—",
                          f"{now - st.last_seen:.0f}"))
        items.sort(key=lambda x: x[0])
        for it in items:
            self.tree.insert("", "end", values=it)

    # ==================================================================
    def _on_close(self):
        self.running.clear()
        self.sim_running.clear()
        self.stop_decode()
        try:
            self.store.close()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = HFDLApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
