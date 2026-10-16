# -*- coding: utf-8 -*-
"""
HFDL 常用默认配置（源自 OpenHFDL / dumphfdl 官方参数与系统表）。

关键事实（取自 OpenHFDL 源码 src/hfdl.c、src/hfdl.h）：
  · HFDL 符号率固定为 1800 baud（HFDL_SYMBOL_RATE = 1800，内部 SPS = 3）
  · 8 种突发类型（单/双时隙 × 4 种速率）：

        速率    调制    编码率   时隙
        300     BPSK    1/4      单/双
        600     BPSK    1/2      单/双
        1200    QPSK    1/2      单/双
        1800    8PSK    1/2      单/双

  · 地面站频率表来自 OpenHFDL etc/systable.conf（16 个地面站，覆盖 2.9~21.9 MHz）

本模块提供：
  · HFDL_MODES            —— 8 种突发类型的调制参数
  · BAND_RANGES           —— HFDL 标准分波段
  · load_systable()       —— 解析 systable.conf
  · band_channels()       —— 按波段归并频道频率
  · recommend_sample_rate() —— 依据频道跨度推荐 SDR 采样率
  · DECODE_PRESETS        —— 常用解码预设（可直接生成 dumphfdl 命令行）
"""
import os
import re

# ----------------------------------------------------------------------
# 调制/突发参数（严格取自 OpenHFDL src/hfdl.c hfdl_frame_params[]）
# ----------------------------------------------------------------------
HFDL_SYMBOL_RATE = 1800          # 波特率（固定）
HFDL_SPS = 3                     # OpenHFDL 内部每符号采样数

# code_rate 为“每比特占用符号数的倒数”：4 = 1/4，2 = 1/2
HFDL_MODES = [
    dict(bit_rate=300,  modulation="BPSK", code_rate="1/4", slots="single"),
    dict(bit_rate=600,  modulation="BPSK", code_rate="1/2", slots="single"),
    dict(bit_rate=1200, modulation="QPSK", code_rate="1/2", slots="single"),
    dict(bit_rate=1800, modulation="8PSK", code_rate="1/2", slots="single"),
    dict(bit_rate=300,  modulation="BPSK", code_rate="1/4", slots="double"),
    dict(bit_rate=600,  modulation="BPSK", code_rate="1/2", slots="double"),
    dict(bit_rate=1200, modulation="QPSK", code_rate="1/2", slots="double"),
    dict(bit_rate=1800, modulation="8PSK", code_rate="1/2", slots="double"),
]

# 旧版内置解调器使用的简化映射（GUI 下拉用）
LEGACY_MODULATION_MAP = {
    1800: "8PSK",
    1200: "QPSK",
    600: "QPSK",
    300: "QPSK",
}

# ----------------------------------------------------------------------
# 内置（声卡）教学解调器的可选参数
#   HFDL 实际符号率固定 1800 baud；此处额外提供 300/600/1200/2400 档，
#   便于对照实验与教学演示（含 300 baud 低速档）。
# ----------------------------------------------------------------------
BUILTIN_BAUD_RATES = [300, 600, 1200, 1800, 2400]

#   BPSK = 1 bit/符号（HFDL 300/600 bps 使用）
#   QPSK = 2 bit/符号（HFDL 1200 bps 使用）
#   8PSK = 3 bit/符号（HFDL 1800 bps 使用）
BUILTIN_MODULATIONS = ["BPSK", "QPSK", "8PSK"]

# 波特率 -> 对应 HFDL 速率档位提示
BAUD_TO_HFDL_HINT = {
    300: "HFDL 300 bps (BPSK, 编码率 1/4)",
    600: "HFDL 600 bps (BPSK, 编码率 1/2)",
    1200: "HFDL 1200 bps (QPSK, 编码率 1/2)",
    1800: "HFDL 1800 bps (8PSK, 编码率 1/2) — HFDL 标准符号率",
    2400: "非 HFDL 标准档位（教学对照用）",
}

# ----------------------------------------------------------------------
# HFDL 标准分波段（依据系统表实际频率分布归纳）
# ----------------------------------------------------------------------
BAND_RANGES = [
    (2800,  3100,  "2.9 MHz"),
    (3300,  3600,  "3.4 MHz"),
    (3800,  3960,  "3.9 MHz"),
    (4600,  4760,  "4.6 MHz"),
    (5400,  5800,  "5.5 MHz"),
    (6400,  6800,  "6.5 MHz"),
    (8800,  9000,  "8.9 MHz"),
    (10000, 10100, "10.0 MHz"),
    (11100, 11250, "11.1 MHz"),
    (11280, 11420, "11.3 MHz"),
    (13200, 13400, "13.2 MHz"),
    (14900, 15100, "15.0 MHz"),
    (17850, 18000, "17.9 MHz"),
    (21850, 22050, "21.9 MHz"),
]

# SoapySDR 驱动常用预设（按硬件）
SDR_DRIVERS = [
    ("rtlsdr",   "RTL-SDR (需上变频/短波改造)"),
    ("airspyhf", "Airspy HF+"),
    ("sdrplay",  "SDRplay RSP"),
    ("hackrf",   "HackRF"),
    ("lime",     "LimeSDR"),
    ("plutosdr", "PlutoSDR"),
    ("rx888",    "RX-888"),
    ("kiwisdr",  "KiwiSDR (网络 I/Q)"),
    ("file",     "I/Q 文件回放"),
]

# 支持的 SDR 采样率（dumphfdl 文档给出的典型值）
SAMPLE_RATES = [48000, 96000, 192000, 250000, 500000, 1000000, 2000000, 2048000]

# I/Q 文件采样格式（dumphfdl --sample-format）
SAMPLE_FORMATS = ["CS16", "CS8", "CU8", "CF32"]


# ----------------------------------------------------------------------
# 系统表解析
# ----------------------------------------------------------------------
_STATION_RE = re.compile(
    r"\{\s*id\s*=\s*(\d+);\s*"
    r"lat\s*=\s*(-?[\d.]+);\s*"
    r"lon\s*=\s*(-?[\d.]+);\s*"
    r"frequencies\s*=\s*\(([^)]*)\);\s*"
    r'name\s*=\s*"([^"]*)"\s*;',
    re.S,
)


def default_systable_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "systable.conf")


def load_systable(path=None):
    """解析 OpenHFDL 的 systable.conf（libconfig 子集）。

    返回 [{'id':int, 'lat':float, 'lon':float, 'freqs':[float,...], 'name':str}, ...]
    """
    path = path or default_systable_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
    except Exception:
        return []
    out = []
    for m in _STATION_RE.finditer(txt):
        sid = int(m.group(1))
        lat = float(m.group(2))
        lon = float(m.group(3))
        freqs = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.group(4))]
        name = m.group(5).strip()
        out.append(dict(id=sid, lat=lat, lon=lon, freqs=freqs, name=name))
    return out


def band_of(freq_khz):
    """返回频率所属波段名（未匹配则 None）。"""
    for lo, hi, name in BAND_RANGES:
        if lo <= freq_khz <= hi:
            return name
    return None


def band_channels(stations=None):
    """按波段归并所有地面站频率。

    返回 OrderedDict: 波段名 -> 排序去重后的频率列表(kHz)
    """
    if stations is None:
        stations = load_systable()
    bands = {name: set() for _, _, name in BAND_RANGES}
    other = set()
    for st in stations:
        for f in st["freqs"]:
            b = band_of(f)
            if b:
                bands[b].add(f)
            else:
                other.add(f)
    out = []
    for _, _, name in BAND_RANGES:
        if bands[name]:
            out.append((name, sorted(bands[name])))
    if other:
        out.append(("其他", sorted(other)))
    return out


def recommend_sample_rate(freqs_khz, margin_khz=25.0):
    """依据频道频率跨度推荐可覆盖全部频道的最小 SDR 采样率。"""
    if not freqs_khz:
        return 250000
    span = max(freqs_khz) - min(freqs_khz) + 2 * margin_khz
    need = span * 1000.0
    for sr in [48000, 96000, 192000, 250000, 500000, 1000000, 2000000, 2048000]:
        if sr >= need:
            return sr
    return 2048000


def stations_on_freq(stations, freq_khz, tol=0.01):
    """返回使用某频率的地面站名称列表。"""
    return [st["name"] for st in stations
            if any(abs(f - freq_khz) <= tol for f in st["freqs"])]


# ----------------------------------------------------------------------
# 常用解码预设
# ----------------------------------------------------------------------
def build_decode_presets():
    """生成 GUI 可选的解码预设列表。

    每项：name / freqs(kHz) / sample_rate / desc

    注意：dumphfdl 要求所有频道都落在 SDR 采样带宽内，因此预设以“单波段”
    为单位（同一波段内频道跨度通常 < 250 kHz）。跨波段需要多路 SDR 或多次运行。
    """
    stations = load_systable()
    presets = []

    # 单波段预设：覆盖该波段全部频道
    for name, freqs in band_channels(stations):
        if name == "其他":
            continue
        presets.append(dict(
            name=f"{name} 全波段（{len(freqs)} 频道）",
            freqs=freqs,
            sample_rate=recommend_sample_rate(freqs),
            desc=f"覆盖 {min(freqs)}–{max(freqs)} kHz",
        ))

    return presets


def format_defaults_text():
    """返回常用默认配置的文本说明（GUI 展示 / README 引用）。"""
    lines = []
    lines.append(f"符号率: {HFDL_SYMBOL_RATE} baud (固定)")
    lines.append("突发类型: " + ", ".join(
        f"{m['bit_rate']}bps/{m['modulation']}({m['code_rate']},{m['slots']})"
        for m in HFDL_MODES[:4]))
    lines.append("标准波段: " + ", ".join(n for _, _, n in BAND_RANGES))
    return "\n".join(lines)


if __name__ == "__main__":
    st = load_systable()
    print(f"地面站: {len(st)}")
    for name, freqs in band_channels(st):
        print(f"  {name:10s} {len(freqs):2d} 频道  "
              f"推荐采样率 {recommend_sample_rate(freqs)}  "
              f"({min(freqs)}–{max(freqs)} kHz)")
    print()
    for p in build_decode_presets():
        print(f"预设: {p['name']}  sr={p['sample_rate']}  n={len(p['freqs'])}")
    print()
    print(format_defaults_text())
