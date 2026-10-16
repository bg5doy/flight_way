# -*- coding: utf-8 -*-
"""全局配置。

包含两部分：
  1. OpenHFDL (dumphfdl) 解码器参数 —— 主用解码后端（成熟库）
  2. 内置教学解调器参数 —— 可选备用（声卡音频输入）

HFDL 权威默认值（取自 OpenHFDL src/hfdl.h / src/hfdl.c）：
  符号率固定 1800 baud；300/600 bps 用 BPSK，1200 bps 用 QPSK，1800 bps 用 8PSK。
"""
from dataclasses import dataclass, field, asdict

# 解码后端
BACKEND_OPENHFDL_LOCAL = "openhfdl-local"     # I/Q 录制文件 → dumphfdl（离线解码）
BACKEND_OPENHFDL_SOUNDCARD = "openhfdl-soundcard"  # 声卡/ SDR IQ OUT → I/Q → dumphfdl
BACKEND_OPENHFDL_REMOTE = "openhfdl-remote"   # 连接远端 dumphfdl 的 TCP 输出

# 已移除的后端（保留常量仅供读取旧配置时兼容，界面不再提供）：
#   · builtin-soundcard：内置教学解调与真实 HFDL 非位级兼容，解不出实际报文
#   · SoapySDR 直连：本机 SDRuno 独占 SDRplay，且 dumphfdl 在 WSL 内无法访问 USB
BACKEND_BUILTIN_SOUNDCARD = "builtin-soundcard"      # 已弃用
BACKEND_SOAPYSDR_DIRECT = "soapysdr-direct"          # 已弃用

BACKEND_LABELS = {
    BACKEND_OPENHFDL_LOCAL: "OpenHFDL + I/Q 文件（离线解码）",
    BACKEND_OPENHFDL_SOUNDCARD: "OpenHFDL + 声卡桥接（声卡 / SDR IQ OUT）",
    BACKEND_OPENHFDL_REMOTE: "OpenHFDL 远程解码 (TCP JSON)",
}

# 常用 HFDL 默认配置（与 hfdl_presets 保持一致，避免循环导入故此处内联）
HFDL_SYMBOL_RATE = 1800
HFDL_BIT_RATES = [300, 600, 1200, 1800]


@dataclass
class HfdlConfig:
    # ==================================================================
    # 一、OpenHFDL (dumphfdl) —— 主用解码后端
    # ==================================================================
    backend: str = BACKEND_OPENHFDL_LOCAL

    # 输入源：留空 iq_file 表示使用 SoapySDR 实时 SDR
    oh_iq_file: str = ""                       # I/Q 录制文件路径（离线解码）
    oh_sample_format: str = "CS16"              # I/Q 采样格式: CS16/CS8/CU8/CF32
    oh_sdr_driver: str = "rtlsdr"               # SoapySDR 驱动: rtlsdr/airspyhf/sdrplay/...
    oh_sdr_opts: str = ""                       # 追加驱动参数, 如 serial=123456
    oh_sample_rate: int = 250000                # SDR 采样率 (Hz)
    oh_centerfreq: float = 0.0                  # 中心频率 kHz（0=由频道自动计算）

    # 频道与系统表
    oh_freqs: list = field(default_factory=lambda: [8825.0, 8834.0, 8843.0, 8885.0,
                                                    8894.0, 8912.0, 8921.0, 8927.0,
                                                    8936.0, 8939.0, 8942.0, 8948.0,
                                                    8957.0, 8977.0])
    oh_freqs_text: str = ""                     # 手工输入频道（空格分隔，优先于预设）
    oh_system_table: str = ""                   # systable.conf 路径（空=项目内默认）
    oh_wsl_distro: str = ""                     # 使用 WSL 时的发行版名（空=自动）
    oh_binary: str = ""                         # 手工指定 dumphfdl 路径

    # 输出
    oh_output_format: str = "json"              # json / basestation / text
    oh_utc: bool = True
    oh_milliseconds: bool = False
    oh_station_id: str = ""                     # 本站标识（写入 JSON 的 station 字段）
    oh_output_mpdus: bool = False

    # 远程模式
    oh_remote_host: str = "127.0.0.1"
    oh_remote_port: int = 5555

    # ---- 声卡 → I/Q 桥接（让 OpenHFDL 也能用声卡）----
    #   source: audio  —— 声卡音频（实信号），需希尔伯特变换成解析 I/Q
    #           sdr-iq —— 真正的复基带 I/Q（SDRuno IQ OUT / 声卡 SDR）：
    #                     立体声左右声道即 I/Q，直接透传，无需变换
    #   mode: stereo-iq（左右声道=I/Q）
    #         analytic （实音频→希尔伯特解析信号，推荐）
    #         real-i   （实音频直接作 I，Q=0，有镜像）
    oh_bridge_source: str = "audio"
    oh_bridge_mode: str = "analytic"
    oh_bridge_device: int = -1          # sounddevice 输入设备索引
    oh_bridge_rate: int = 48000         # 采样率（audio=声卡率；sdr-iq=IQ OUT 率，如 192000）
    oh_bridge_audio_center: float = 1370.0   # HFDL 音频中心频率 Hz（仅 audio 源用）
    oh_bridge_window: int = 511         # 希尔伯特 FIR 长度（奇数；127 在低频段不足）
    # sdr-iq 源：VFO/中心频率（kHz）；频道改用 systable 的 HFDL 射频频率
    oh_iq_vfo_khz: float = 8927.0
    # 带内自动搜索：像 PC-HFDL / KG-HFDL 那样不必预知信号在音频中的位置。
    # 在 [from,to] 内按 step 生成一组频道交给 dumphfdl，命中者即可解出。
    oh_bridge_scan: bool = True
    oh_bridge_scan_from: float = 400.0
    oh_bridge_scan_to: float = 2900.0
    oh_bridge_scan_step: float = 200.0

    # ==================================================================
    # 二、内置教学解调器（声卡音频，可选备用）
    #   注：真实 HFDL 建议用 OpenHFDL；此路径仅用于演示/教学。
    # ==================================================================
    device_index: int = -1          # sounddevice 输入设备索引，-1=默认
    sample_rate: int = 48000        # 声卡采样率

    center_freq: float = 1370.0     # 音频中心频率 Hz（SSB 输出中的 HFDL 位置）
    bandwidth: float = 1800.0       # 信号带宽 Hz
    bandpass: bool = False          # 输入带通预滤波

    symbol_rate: int = HFDL_SYMBOL_RATE   # 波特率（HFDL 固定 1800）
    modulation: str = "8PSK"        # 8PSK / QPSK
    descrambler: bool = True        # V.29 解扰
    scrambler_seed: int = 0x0000
    gain: float = 1.0

    # ---- 帧结构（内置教学帧）----
    sync_byte: int = 0xF3
    crc_poly: int = 0x1021
    crc_init: int = 0x1D0F
    block_bits: int = 56
    demode: int = 2

    # ==================================================================
    # 三、应用层
    # ==================================================================
    stale_minutes: int = 60         # 飞机过期擦除（分钟）
    track_minutes: int = 120        # 历史航迹保留（分钟），上限 960 = 16 小时
    track_show: bool = True         # 地图上显示历史航迹
    label_mode: str = "auto"        # 航班标签：auto=按缩放自动 / always / never
    icon_px: float = 15.0           # 飞机图标像素尺寸（全球视图基准）
    db_path: str = "hfdl_flights.db"
    station_id: str = "STATION-1"

    def copy(self):
        return HfdlConfig(**asdict(self))

    # ------------------------------------------------------------------
    def effective_freqs(self):
        """返回最终使用的频道频率列表（kHz）。"""
        if self.oh_freqs_text.strip():
            out = []
            for tok in self.oh_freqs_text.replace(",", " ").split():
                try:
                    out.append(float(tok))
                except ValueError:
                    pass
            if out:
                return out
        return list(self.oh_freqs or [])
