# -*- coding: utf-8 -*-
"""
OpenHFDL (dumphfdl) 集成模块。

OpenHFDL 是成熟的 C/C++ HFDL 解码器，由 `dumphfdl` 提供：
  · 通过 SoapySDR 直接驱动 SDR 硬件（RTL-SDR / Airspy HF+ / SDRplay / ...），
    也支持回放 I/Q 文件（--iq-file）
  · 内部完成 HFDL 物理层解调（1800 baud，BPSK/QPSK/8PSK 自适应）、
    去交织、R-S 纠错、LPDU/SPDU/HFNPDU 解析，并调用 libacars 解析 ACARS
  · 通过 --output 输出：text / json / basestation，目标可为 file/tcp/udp/zmq

本模块只做“集成”，不重复实现解调：
  1. 定位 dumphfdl（本机 PATH、项目内 bin/、或 WSL 内的 Linux 版）
  2. 依据 HFDL 常用默认配置拼装命令行（频道、采样率、系统表、输出）
  3. 以子进程运行，实时按行读取 JSON / basestation 输出并解析
  4. 也支持作为消费者连接远程 dumphfdl 的 TCP JSON 流
  5. 把解析结果转换成 HfdlFrame，供地图与数据库复用

JSON 结构（取自 OpenHFDL src/fmtr-json.c 与 libacars 序列化）：
  {"app":{"name":"dumphfdl","ver":"..."},
   "t":{"sec":...,"usec":...},
   "freq":11384,"bit_rate":1800,"sig_level":...,"noise_level":...,
   "freq_skew":...,"slot":"S",
   ...协议树节点(如 "hfnpdu": {"type":{"name":...},"flight_id":...,
                              "pos":{"lat":..,"lon":..}}) ...}
位置字段可能出现在多层嵌套中，因此采用“容错递归取值”。
"""
import json
import os
import re
import socket
import subprocess
import threading
import time

import hfdl_presets as presets

try:
    from hfdl_frame import HfdlFrame
except Exception:                                    # pragma: no cover
    HfdlFrame = None


# ----------------------------------------------------------------------
# dumphfdl 定位
# ----------------------------------------------------------------------
def to_wsl_path(p):
    """把 Windows 路径转换为 WSL 路径：D:\\a\\b -> /mnt/d/a/b"""
    if not p:
        return p
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", str(p))
    if m:
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return str(p).replace("\\", "/")


def _which(cmd):
    from shutil import which
    return which(cmd)


def find_dumphfdl(explicit=None):
    """返回 (kind, path) —— kind ∈ {'native','wsl',None}。

    native: 可直接在 Windows 上执行的 dumphfdl.exe
    wsl   : 位于 WSL 内的 Linux 二进制（用 wsl 命令调用）
    """
    cands = []
    if explicit:
        cands.append(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [
        os.path.join(here, "bin", "dumphfdl.exe"),
        os.path.join(here, "bin", "dumphfdl"),
        os.path.join(here, "openhfdl", "bin", "dumphfdl"),
        r"C:\Program Files\dumphfdl\dumphfdl.exe",
        r"C:\dumphfdl\dumphfdl.exe",
    ]
    w = _which("dumphfdl") or _which("dumphfdl.exe")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c):
            return ("native", c)

    # WSL 内查找
    p = find_dumphfdl_in_wsl()
    if p:
        return ("wsl", p)
    return (None, None)


def _wsl_distros():
    try:
        out = subprocess.run(["wsl", "-l", "-q"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        s = line.replace("\x00", "").strip()
        if s and not s.lower().startswith("windows"):
            names.append(s)
    return names


def find_dumphfdl_in_wsl():
    """在 WSL 中查找已编译好的 dumphfdl。"""
    search = (
        "command -v dumphfdl 2>/dev/null; "
        "ls -1 /mnt/d/Temp/aihfdl/openhfdl/szpajder-dumphfdl-bb7234b/build/src/dumphfdl "
        "2>/dev/null; "
        "ls -1 /usr/local/bin/dumphfdl 2>/dev/null"
    )
    for distro in (_wsl_distros() or ["Ubuntu-20.04"]):
        try:
            cmd = ["wsl", "-d", distro, "--", "bash", "-lc", search]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            for line in (r.stdout or "").splitlines():
                p = line.strip()
                if p:
                    return p
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------
# JSON 容错解析
# ----------------------------------------------------------------------
_NAME_RE = re.compile(r"[^.\[]+$")


def _flatten(obj, path="", out=None):
    """把嵌套 JSON 展平为 {路径: 叶子值}。"""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(v, f"{path}[{i}]", out)
    else:
        out[path] = obj
    return out


def _leaf(path):
    m = _NAME_RE.search(path)
    return m.group(0).lower() if m else path.lower()


def _pick(flat, names, cast=None, rng=None):
    """按叶子名（不区分大小写）取第一个命中值。"""
    want = {n.lower() for n in names}
    for path, val in flat.items():
        if _leaf(path) in want:
            try:
                v = cast(val) if cast else val
            except Exception:
                continue
            if rng is not None:
                lo, hi = rng
                if not (lo <= v <= hi):
                    continue
            return v
    return None


def parse_openhfdl_json(line):
    """解析 dumphfdl 的一行 JSON 输出 -> dict（统一字段）。"""
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    flat = _flatten(obj)

    rec = {}
    # ---- 元数据 ----
    rec["ts"] = _pick(flat, ["sec"], float)
    usec = _pick(flat, ["usec"], float)
    if rec["ts"] is not None and usec:
        rec["ts"] += usec / 1e6
    if rec["ts"] is None:
        rec["ts"] = time.time()
    rec["freq_khz"] = _pick(flat, ["freq"], float)
    rec["bit_rate"] = _pick(flat, ["bit_rate", "bitrate"], int)
    rec["sig_level"] = _pick(flat, ["sig_level"], float)
    rec["noise_level"] = _pick(flat, ["noise_level"], float)
    rec["freq_skew"] = _pick(flat, ["freq_skew"], float)
    rec["slot"] = _pick(flat, ["slot"], str)
    rec["ground_station"] = _pick(flat, ["gs", "ground_station", "station_name"], str)
    rec["station_id"] = _pick(flat, ["station_id", "gs_id"], int)

    # ---- 航班/飞机 ----
    rec["flight"] = _pick(flat, ["flight_id", "flight", "callsign", "ac_id"], str)
    rec["reg"] = _pick(flat, ["reg", "tail", "registration", "ac_reg"], str)
    rec["icao"] = _pick(flat, ["icao", "icao_addr", "hexid", "addr", "aircraft_id"], str)
    if isinstance(rec["icao"], (int, float)):
        rec["icao"] = f"{int(rec['icao']):06X}"

    # ---- 位置（lat/lon 可能在 pos{} 或顶层，注意排除别名的误命中）----
    rec["lat"] = _pick(flat, ["lat", "latitude"], float, rng=(-90, 90))
    rec["lon"] = _pick(flat, ["lon", "lng", "long", "longitude"], float, rng=(-180, 180))
    rec["altitude"] = _pick(flat, ["alt", "altitude", "fl", "flight_level"], float)
    rec["heading"] = _pick(flat, ["track", "heading", "hdg", "true_track"], float)
    rec["speed"] = _pick(flat, ["gs", "ground_speed", "speed", "tas"], float)
    if rec["heading"] is not None:
        rec["heading"] = float(rec["heading"]) % 360

    # ---- 报文类型/文本摘要 ----
    rec["type"] = _pick(flat, ["name", "type"], str)
    txt = _pick(flat, ["text", "txt", "message", "msg_text"], str)
    rec["text"] = (txt or rec["type"] or "").strip()
    return rec


# ----------------------------------------------------------------------
# Basestation (Kinetic CSV) 解析
#   格式串取自 OpenHFDL src/fmtr-basestation.c:
#   MSG,3,1,1,%06X,1,<date>,<time>,<flight>,,,,<lat>,<lon>,,<freq>,,,,0
# ----------------------------------------------------------------------
def parse_basestation(line):
    line = line.strip()
    if not line.startswith("MSG,"):
        return None
    f = line.split(",")
    if len(f) < 16:
        return None
    rec = dict(ts=time.time(), freq_khz=None, bit_rate=None, sig_level=None,
               noise_level=None, freq_skew=None, slot=None, ground_station=None,
               station_id=None, flight=None, reg=None, icao=None,
               lat=None, lon=None, altitude=None, heading=None, speed=None,
               type="position", text="")
    try:
        rec["icao"] = f[4].strip().upper() or None
        d, t = f[6].strip(), f[7].strip()
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", d)
        if m:
            y, mo, dd = (int(x) for x in m.groups())
            tt = t.split(".")
            hh, mm, ss = (int(x) for x in tt[0].split(":"))
            rec["ts"] = time.mktime((y, mo, dd, hh, mm, ss, 0, 0, -1))
        rec["flight"] = f[8].strip() or None
        lat = float(f[12]); lon = float(f[13])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            rec["lat"], rec["lon"] = lat, lon
        if f[15].strip():
            rec["freq_khz"] = float(f[15])
    except Exception:
        return None
    if rec["lat"] is None:
        return None
    return rec


# ----------------------------------------------------------------------
# 记录 -> HfdlFrame
# ----------------------------------------------------------------------
def record_to_frame(rec, raw_line=""):
    """把解析出的记录转换为 HfdlFrame（复用既有地图/存储）。"""
    if HfdlFrame is None:
        return None
    fr = HfdlFrame()
    fr.ts = float(rec.get("ts") or time.time())
    fr.crc_ok = True
    fr.mode = int(rec.get("bit_rate") or 0)
    fr.label = "HFDL"
    fr.reg = (rec.get("reg") or rec.get("icao") or "") or ""
    fr.flight = (rec.get("flight") or "").strip()
    fr.lat = rec.get("lat")
    fr.lon = rec.get("lon")
    fr.heading = rec.get("heading")
    alt = rec.get("altitude")
    if alt is not None:
        alt = float(alt)
        # 若以飞行高度层给出（<1000），换算为英尺
        fr.altitude = int(alt * 100) if alt < 1000 else int(alt)
    spd = rec.get("speed")
    fr.speed = int(spd) if spd is not None else None
    fr.raw = (raw_line or "").encode("utf-8", "ignore")[:512]
    bits = []
    if rec.get("freq_khz"):
        bits.append(f"{rec['freq_khz']:.1f}kHz")
    if rec.get("bit_rate"):
        bits.append(f"{rec['bit_rate']}bps")
    if rec.get("slot"):
        bits.append(f"slot={rec['slot']}")
    if rec.get("ground_station"):
        bits.append(str(rec["ground_station"]))
    if rec.get("text"):
        bits.append(str(rec["text"])[:60])
    fr.text = " ".join(bits)
    if not fr.key():
        return None
    return fr


# ----------------------------------------------------------------------
# 解码器进程
# ----------------------------------------------------------------------
class OpenHfdlDecoder:
    """启动/管理 dumphfdl 子进程，并把输出解析成 HfdlFrame。

    参数
      cfg           HfdlConfig（含 oh_* 扩展字段）
      on_frame      回调(HfdlFrame)
      on_log        回调(str)
    """

    def __init__(self, cfg, on_frame=None, on_log=None):
        self.cfg = cfg
        self.on_frame = on_frame or (lambda fr: None)
        self.log = on_log or (lambda s: None)
        self.proc = None
        self.thread = None
        self.running = threading.Event()
        self.kind = None
        self.path = None
        self.cmdline = []
        self.stats = dict(frames=0, lines=0, errors=0)

    # ------------------------------------------------------------------
    def resolve(self):
        self.kind, self.path = find_dumphfdl(getattr(self.cfg, "oh_binary", None))
        return self.kind is not None

    # ------------------------------------------------------------------
    def build_command(self):
        """依据配置拼装 dumphfdl 命令行（返回参数列表）。"""
        cfg = self.cfg
        args = []
        wsl = (self.kind == "wsl")

        # 频道频率（先取好，供自动中心频率使用）
        freqs = list(getattr(cfg, "oh_freqs", []) or [])
        if not freqs:
            freqs = [float(x) for x in getattr(cfg, "oh_freqs_text", "").split()]
        if not freqs:
            b = presets.band_channels()
            if b:
                freqs = b[6][1]          # 默认 8.9 MHz 波段
        freqs_txt = [f"{f:g}" for f in freqs]

        def _p(path):
            """Windows 路径 -> WSL 路径（仅 WSL 模式需要）。"""
            return to_wsl_path(path) if wsl else path

        # ---- 输入源 ----
        iq_file = getattr(cfg, "oh_iq_file", "") or ""
        if iq_file:
            args += ["--iq-file", _p(iq_file)]
            args += ["--sample-format", getattr(cfg, "oh_sample_format", "CS16")]
            args += ["--sample-rate", str(int(cfg.oh_sample_rate))]
            # I/Q 文件模式必须给出中心频率；未指定则取频道列表中心
            cf = float(getattr(cfg, "oh_centerfreq", 0.0) or 0.0)
            if cf <= 0 and freqs:
                cf = (min(freqs) + max(freqs)) / 2.0
            args += ["--centerfreq", f"{cf:g}"]
        else:
            driver = getattr(cfg, "oh_sdr_driver", "rtlsdr")
            opts = getattr(cfg, "oh_sdr_opts", "") or ""
            soapy = f"driver={driver}"
            if opts:
                soapy += f",{opts}"
            args += ["--soapysdr", soapy]
            args += ["--sample-rate", str(int(cfg.oh_sample_rate))]
            # SoapySDR 模式可省略中心频率，由 dumphfdl 自动计算

        # ---- 系统表（地面站名称/频率）----
        st = getattr(cfg, "oh_system_table", "") or presets.default_systable_path()
        if os.path.exists(st):
            args += ["--system-table", _p(st)]
        else:
            self.log(f"[openhfdl] 未找到系统表: {st}（仅显示地面站 ID）")

        # ---- 输出：JSON 到 stdout ----
        fmt = getattr(cfg, "oh_output_format", "json")
        args += ["--output", f"decoded:{fmt}:file:path=-"]

        # ---- 其他常用开关 ----
        if getattr(cfg, "oh_utc", True):
            args += ["--utc"]
        if getattr(cfg, "oh_milliseconds", False):
            args += ["--milliseconds"]
        if getattr(cfg, "oh_station_id", ""):
            args += ["--station-id", cfg.oh_station_id]
        if getattr(cfg, "oh_output_mpdus", False):
            args += ["--output-mpdus"]

        # ---- 频道频率（kHz，必须放在最后）----
        args += freqs_txt
        return args

    # ------------------------------------------------------------------
    def start(self):
        if self.running.is_set():
            return True
        if not self.resolve():
            self.log("[openhfdl] 未找到 dumphfdl 可执行文件。"
                     "可用 tools/build_openhfdl.md 中的脚本在 WSL/Linux 构建，"
                     "或点“仿真示例”演示界面。")
            return False

        args = self.build_command()
        if self.kind == "wsl":
            distro = getattr(self.cfg, "oh_wsl_distro", "") or (_wsl_distros() or ["Ubuntu-20.04"])[0]
            cmd = ["wsl", "-d", distro, "--", self.path] + args
        else:
            cmd = [self.path] + args
        self.cmdline = cmd
        self.log("[openhfdl] " + " ".join(
            (f'"{c}"' if " " in c else c) for c in cmd))

        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="ignore", bufsize=1,
            )
        except Exception as e:
            self.log(f"[openhfdl] 启动失败: {e}")
            return False

        self.running.set()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        return True

    # ------------------------------------------------------------------
    def _reader(self):
        fmt = getattr(self.cfg, "oh_output_format", "json")
        try:
            for line in self.proc.stdout:
                if not self.running.is_set():
                    break
                line = line.rstrip("\r\n")
                if not line:
                    continue
                self.stats["lines"] += 1
                rec = None
                if fmt == "json":
                    if line.lstrip().startswith("{"):
                        rec = parse_openhfdl_json(line)
                    else:
                        self.log("[dumphfdl] " + line[:200])
                        continue
                elif fmt == "basestation":
                    rec = parse_basestation(line)
                else:
                    self.log("[dumphfdl] " + line[:200])
                    continue
                if not rec:
                    self.stats["errors"] += 1
                    continue
                self.stats["frames"] += 1
                fr = record_to_frame(rec, line)
                if fr is not None:
                    try:
                        self.on_frame(fr)
                    except Exception as e:
                        self.log(f"[openhfdl] 回调异常: {e}")
        except Exception as e:
            self.log(f"[openhfdl] 读取输出异常: {e}")
        finally:
            self.log("[openhfdl] 解码进程输出结束")

    # ------------------------------------------------------------------
    def stop(self):
        self.running.clear()
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
        self.log("[openhfdl] 已停止")


# ----------------------------------------------------------------------
# 远程 dumphfdl JSON/TCP 消费者
# ----------------------------------------------------------------------
class RemoteJsonFeed:
    """连接远端 dumphfdl 的 TCP 输出（--output decoded:json:tcp:...）。"""

    def __init__(self, host, port, on_frame=None, on_log=None, fmt="json"):
        self.host = host
        self.port = int(port)
        self.fmt = fmt
        self.on_frame = on_frame or (lambda fr: None)
        self.log = on_log or (lambda s: None)
        self.sock = None
        self.thread = None
        self.running = threading.Event()
        self.stats = dict(frames=0, lines=0, errors=0)

    def start(self):
        if self.running.is_set():
            return True
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
            self.sock.settimeout(1.0)
        except Exception as e:
            self.log(f"[remote] 连接 {self.host}:{self.port} 失败: {e}")
            return False
        self.running.set()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self.log(f"[remote] 已连接 {self.host}:{self.port}（{self.fmt}）")
        return True

    def _reader(self):
        buf = b""
        while self.running.is_set():
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except Exception as e:
                self.log(f"[remote] 接收异常: {e}")
                break
            if not chunk:
                self.log("[remote] 远端已关闭连接")
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "ignore").strip()
                if not text:
                    continue
                self.stats["lines"] += 1
                rec = parse_openhfdl_json(text) if self.fmt == "json" \
                    else parse_basestation(text)
                if not rec:
                    self.stats["errors"] += 1
                    continue
                self.stats["frames"] += 1
                fr = record_to_frame(rec, text)
                if fr is not None:
                    try:
                        self.on_frame(fr)
                    except Exception as e:
                        self.log(f"[remote] 回调异常: {e}")

    def stop(self):
        self.running.clear()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.log("[remote] 已断开")


# ----------------------------------------------------------------------
# 自检：用真实 dumphfdl 的示例 JSON 校验解析器
# ----------------------------------------------------------------------
_SAMPLE_JSON = [
    # HFNPDU 性能数据（含位置）
    '{"app":{"name":"dumphfdl","ver":"1.7.0"},"t":{"sec":1700000000,"usec":123456},'
    '"freq":11384,"bit_rate":1800,"sig_level":-42.5,"noise_level":-55.1,'
    '"freq_skew":12.3,"slot":"S","hfnpdu":{"type":{"id":13,"name":"performance data"},'
    '"err":false,"version":1,"flight_id":"QF1234","pos":{"lat":-33.9431,"lon":151.1783},'
    '"time":{"hour":10,"min":20,"sec":30}}}',
    # HFNPDU 频率数据
    '{"app":{"name":"dumphfdl","ver":"1.7.0"},"t":{"sec":1700000001,"usec":0},'
    '"freq":8927,"bit_rate":300,"sig_level":-50.0,"noise_level":-60.0,'
    '"freq_skew":-3.2,"slot":"S","hfnpdu":{"type":{"id":12,"name":"frequency data"},'
    '"flight_id":"BAW256","pos":{"lat":51.4700,"lon":-0.4543}}}',
    # ACARS（无位置）
    '{"app":{"name":"dumphfdl","ver":"1.7.0"},"t":{"sec":1700000002,"usec":5},'
    '"freq":13312,"bit_rate":1200,"sig_level":-47.0,"noise_level":-58.0,'
    '"freq_skew":1.1,"slot":"D","lpdu":{"acars":{"reg":"B-2032","flight":"CA981",'
    '"label":"H1","text":"POS N40.0 E116.5"}}}',
]


def _selftest():
    print("== OpenHFDL 集成自检 ==")
    print(f"dumphfdl: {find_dumphfdl()}")
    print()
    ok = 0
    for i, s in enumerate(_SAMPLE_JSON, 1):
        rec = parse_openhfdl_json(s)
        fr = record_to_frame(rec, s) if rec else None
        print(f"样例{i}: {rec.get('flight')} {rec.get('reg')} "
              f"freq={rec.get('freq_khz')} rate={rec.get('bit_rate')} "
              f"pos=({rec.get('lat')},{rec.get('lon')})")
        if fr is not None:
            ok += 1
    print()
    bs = ("MSG,3,1,1,780ABC,1,2024/05/01,12:34:56.000,MU510,,,,31.250000,"
          "121.450000,,11384,,,,0")
    rec = parse_basestation(bs)
    print("basestation:", rec.get("flight"), rec.get("icao"),
          rec.get("lat"), rec.get("lon"))
    print(f"\n解析成功 {ok + (1 if rec else 0)} 条")
    return ok == len(_SAMPLE_JSON) and rec is not None


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
