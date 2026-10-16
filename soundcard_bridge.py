# -*- coding: utf-8 -*-
"""
声卡 → OpenHFDL 桥接器。

背景（已核对 OpenHFDL 源码）：
    dumphfdl 只有两种输入：`--soapysdr`（SDR 硬件）与 `--iq-file`（I/Q 文件，
    支持 `-` 表示标准输入）。**它没有任何声卡/ALSA/PortAudio 输入驱动**，
    且所有采样格式（CU8/CS16/CF32）都是复数 I/Q，不接受实数音频。

因此「声卡 + OpenHFDL」只能通过「桥接」实现：把声卡数据转换成 CS16 I/Q，
经 stdin 灌给 `dumphfdl --iq-file -`。本模块负责这件事。

三种输入模式：
  1. stereo-iq  声卡左右声道本身就是 I/Q —— 声卡 SDR（SoftRock / FiFi-SDR /
                SDRplay 的模拟 I/Q 输出等）。**最理想，无损**。
  2. analytic   单声道实音频（SSB 接收机音频输出）→ 希尔伯特变换得到解析
                信号（负频置零），再作为 I/Q。**推荐用于纯音频源**。
  3. real-i     单声道实音频直接当 I，Q=0。最简单，但存在镜像
                （靠 dumphfdl 的信道滤波抑制），有约 3 dB 损失。

频率约定：
    dumphfdl 里 `--centerfreq 0` + 频道频率 = 音频中心频率(kHz)，例如 HFDL
    音频中心 1370 Hz → 频道写 `1.37`。这样信道化器会把该音频频率搬到 0 Hz。

约束（源码核对）：
    · 采样率必须 ≥ HFDL_SYMBOL_RATE × SPS = 1800 × 3 = 5400 sps
    · 采样格式仅 CU8 / CS16 / CF32
    · 频道与中心频率距离必须 < 采样率/2（check_frequency_span）
"""
import argparse
import os
import subprocess
import sys
import threading
import time

import numpy as np
from scipy.signal import lfilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hfdl_presets as presets
import audio as aud

MIN_SAMPLE_RATE = 5400

# dumphfdl 硬编码的 SSB 载波偏置（src/hfdl.c:46）：
#   freq_shift = centerfreq - (channel_freq + HFDL_SSB_CARRIER_OFFSET_HZ)  (src/hfdl.c:476)
# 即 dumphfdl 认为 HFDL 信号中心在「频道频率 + 1440 Hz」处。
HFDL_SSB_CARRIER_OFFSET_HZ = 1440

MODE_STEREO_IQ = "stereo-iq"
MODE_ANALYTIC = "analytic"
MODE_REAL_I = "real-i"
MODES = [MODE_STEREO_IQ, MODE_ANALYTIC, MODE_REAL_I]

MODE_LABELS = {
    MODE_STEREO_IQ: "立体声 I/Q（声卡 SDR，左右声道= I/Q）",
    MODE_ANALYTIC: "单声道实音频 → 解析信号（希尔伯特）",
    MODE_REAL_I: "单声道实音频直接作 I（Q=0，有镜像）",
}

# ----------------------------------------------------------------------
# 信号来源
#   audio  : 声卡/接收机**音频**（实信号）——需希尔伯特变换成解析 I/Q，
#            命令用 --centerfreq 1.44 + 频道=音频频率(kHz)
#   sdr-iq : **真正的复基带 I/Q**，例如 SDRuno 的 IQ OUT
#            （官方手册第 63 页：I 走左声道、Q 走右声道，最高 192 kHz）
#            ——直接透传，无需变换，是 dumphfdl 的原生输入形态；
#            命令用 --centerfreq <VFO kHz> + 频道=HFDL 射频频率(kHz)
# ----------------------------------------------------------------------
SOURCE_AUDIO = "audio"
SOURCE_SDR_IQ = "sdr-iq"
SOURCES = [SOURCE_AUDIO, SOURCE_SDR_IQ]
SOURCE_LABELS = {
    SOURCE_AUDIO: "声卡/接收机音频（实信号）",
    SOURCE_SDR_IQ: "SDR I/Q（SDRuno IQ OUT 等）",
}


# ----------------------------------------------------------------------
# 希尔伯特变换（流式 FIR，反对称奇对称 + 窗）
# ----------------------------------------------------------------------
def hilbert_fir(n=127, window="hamming"):
    """返回 (h, group_delay)。h 为希尔伯特变换器系数。"""
    if n % 2 == 0:
        n += 1
    c = (n - 1) // 2
    k = np.arange(n) - c
    h = np.zeros(n)
    odd = (k % 2) != 0
    h[odd] = 2.0 / (np.pi * k[odd])
    if window == "hamming":
        h *= np.hamming(n)
    elif window == "blackman":
        h *= np.blackman(n)
    return h, c


class HilbertIQ:
    """流式 实信号 → 解析信号(I/Q)。I 路按群延迟对齐。

    抽头数很关键：HFDL 音频落在 400–2900 Hz（归一化频率很低），短 FIR 的
    希尔伯特变换在低频段过渡带占比过大，会造成镜像泄漏与幅度起伏。
    实测（48 kHz）：
        n=127 : 300 Hz 镜像仅 −16 dB、幅度起伏 2.8 dB  ← 明显失真
        n=511 : 300 Hz −63 dB、起伏 0.013 dB          ← 可用
        n=1023: 全频段 ≤ −66 dB
    因此默认 n=511。
    """

    def __init__(self, n=511, window="hamming"):
        self.h, self.c = hilbert_fir(n, window)
        self.zi = np.zeros(len(self.h) - 1)
        self.delay = np.zeros(self.c)

    def process(self, x):
        q, self.zi = lfilter(self.h, [1.0], x, zi=self.zi)
        if self.c > 0:
            buf = np.concatenate([self.delay, x])
            i_out = buf[:len(x)]
            self.delay = buf[len(x):]
        else:
            i_out = x
        return i_out, q


# ----------------------------------------------------------------------
# 采样格式转换
# ----------------------------------------------------------------------
def to_cs16(i, q, scale=32767.0):
    """(I,Q) float [-1,1] → 交织 int16 小端字节流 (CS16)。"""
    i16 = np.clip(np.asarray(i) * scale, -32768, 32767).astype("<i2")
    q16 = np.clip(np.asarray(q) * scale, -32768, 32767).astype("<i2")
    out = np.empty(2 * len(i16), dtype="<i2")
    out[0::2] = i16
    out[1::2] = q16
    return out.tobytes()


# ----------------------------------------------------------------------
# 桥接器
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 健壮的 WAV 读取（SDRuno 的 I/Q 录制可能是 float32，wave 模块读不了）
# ----------------------------------------------------------------------
def read_wav_any(path, max_seconds=None):
    """读取 WAV，返回 (float64 数组[N] 或 [N,ch], rate, channels, fmt_name)。

    支持 PCM u8 / s16 / s32 与 IEEE float32 / float64。
    """
    import struct
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("不是合法的 WAV 文件")
        fmt_tag = None
        channels = rate = bits = 0
        data = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, csz = struct.unpack("<4sI", hdr)
            if cid == b"fmt ":
                body = f.read(csz)
                (fmt_tag, channels, rate, _br, _ba, bits) = struct.unpack(
                    "<HHIIHH", body[:16])
                if csz > 16:
                    f.seek(csz - 16, 1)
            elif cid == b"data":
                if max_seconds:
                    csz = min(csz, int(csz))
                data = f.read(csz)
            else:
                f.seek(csz, 1)
            if csz % 2:
                f.seek(1, 1)
    if data is None or fmt_tag is None:
        raise ValueError("WAV 缺少 fmt 或 data 块")
    if fmt_tag == 1:          # PCM
        if bits == 8:
            a = (np.frombuffer(data, dtype="<u1").astype(np.float64) - 128) / 128.0
            fmt = "PCM8"
        elif bits == 16:
            a = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
            fmt = "PCM16"
        elif bits == 32:
            a = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
            fmt = "PCM32"
        else:
            raise ValueError(f"不支持的 PCM 位深: {bits}")
    elif fmt_tag == 3:        # IEEE float
        dt = "<f4" if bits == 32 else "<f8"
        a = np.frombuffer(data, dtype=dt).astype(np.float64)
        fmt = f"FLOAT{bits}"
    elif fmt_tag == 0xFFFE:   # WAVE_FORMAT_EXTENSIBLE -> 看子格式
        dt = "<f4" if bits == 32 else "<i2"
        a = np.frombuffer(data, dtype=dt).astype(np.float64)
        a = a / 32768.0 if bits == 16 else a
        fmt = f"EXT{bits}"
    else:
        raise ValueError(f"不支持的 WAV 格式标签: {fmt_tag}")
    if channels > 1:
        n = len(a) // channels
        a = a[:n * channels].reshape(n, channels)
    else:
        a = a.reshape(-1, 1)
    if rate < 1000:
        raise ValueError(f"异常采样率: {rate}")
    return a, rate, channels, fmt


class SoundcardBridge:
    """采集声卡并输出 CS16 I/Q。

    sink 可以是：
      · 文件对象（写 bytes）
      · subprocess 的 stdin
      · None（仅统计，用于自测）
    """

    def __init__(self, device_index=-1, sample_rate=48000, mode=MODE_ANALYTIC,
                 blocksize=4096, sink=None, scale=0.9, log=None, exclusive=False):
        if sample_rate < MIN_SAMPLE_RATE:
            raise ValueError(
                f"采样率 {sample_rate} 低于 dumphfdl 下限 {MIN_SAMPLE_RATE} sps")
        if mode not in MODES:
            raise ValueError(f"未知模式: {mode}")
        self.device_index = device_index
        self.sample_rate = int(sample_rate)
        self.mode = mode
        self.blocksize = int(blocksize)
        self.sink = sink
        self.scale = float(scale)
        self.exclusive = bool(exclusive)
        self.log = log or (lambda s: None)
        self.running = threading.Event()
        self.stream = None
        self.hil = HilbertIQ() if mode == MODE_ANALYTIC else None
        self.stats = dict(blocks=0, samples=0, bytes=0)

    # ------------------------------------------------------------------
    def channels(self):
        return 2 if self.mode == MODE_STEREO_IQ else 1

    # ------------------------------------------------------------------
    def _convert(self, block):
        """声卡块(float32) → (I,Q) float 复数分量。"""
        if self.mode == MODE_STEREO_IQ:
            if block.ndim == 1:
                block = block.reshape(-1, 1)
            if block.shape[1] < 2:
                raise RuntimeError("立体声 I/Q 模式需要 2 通道输入")
            return block[:, 0].astype(np.float64), block[:, 1].astype(np.float64)
        x = block[:, 0].astype(np.float64) if block.ndim > 1 else block.astype(np.float64)
        if self.mode == MODE_ANALYTIC:
            return self.hil.process(x)
        # real-i
        return x, np.zeros_like(x)

    # ------------------------------------------------------------------
    def feed(self, audio_block):
        """直接喂入一块音频（供无硬件自测）。返回写入字节数。"""
        i, q = self._convert(np.asarray(audio_block, dtype=np.float64))
        data = to_cs16(i, q, self.scale)
        self.stats["blocks"] += 1
        self.stats["samples"] += len(i)
        self.stats["bytes"] += len(data)
        if self.sink is not None:
            try:
                self.sink.write(data)
                if hasattr(self.sink, "flush"):
                    self.sink.flush()
            except (BrokenPipeError, OSError) as e:
                self.log(f"[bridge] 写入失败(下游已关闭?): {e}")
                self.running.clear()
                return 0
        return len(data)

    # ------------------------------------------------------------------
    def _callback(self, indata, frames, t, status):
        if not self.running.is_set():
            return
        if status:
            self.log(f"[bridge] 声卡告警: {status}")
        self.feed(indata.copy())

    def start(self):
        import sounddevice as sd
        kwargs = dict(
            samplerate=self.sample_rate,
            device=(self.device_index if self.device_index >= 0 else None),
            channels=self.channels(),
            dtype="float32",
            blocksize=self.blocksize,
            callback=self._callback,
        )
        extra = None
        if self.exclusive:
            try:
                api = aud.hostapi_name(sd.query_devices(self.device_index)["hostapi"])
                if "WASAPI" in str(api):
                    extra = sd.WasapiSettings(exclusive=True)
                    kwargs["extra_settings"] = extra
            except Exception as e:
                self.log(f"[bridge] 独占模式不可用: {e}")
        try:
            self.stream = sd.InputStream(**kwargs)
        except Exception as e:
            if extra is not None:
                self.log(f"[bridge] 独占模式打开失败({e})，改用共享模式")
                kwargs.pop("extra_settings", None)
                self.stream = sd.InputStream(**kwargs)
            else:
                raise
        self.running.set()
        self.stream.start()
        api = ""
        try:
            api = aud.hostapi_name(sd.query_devices(self.device_index)["hostapi"])
        except Exception:
            pass
        self.log(f"[bridge] 声卡已打开: dev={self.device_index} {api} "
                 f"{self.sample_rate} Hz × {self.channels()}ch, 模式={self.mode}"
                 f"{' 独占' if extra is not None else ''}")
        return True

    def stop(self):
        self.running.clear()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


# ----------------------------------------------------------------------
# dumphfdl 命令行（stdin 模式）
# ----------------------------------------------------------------------
def build_stdin_command(soundcard_rate, audio_center_hz, cfg=None,
                        system_table=None, output_format="json",
                        utc=True, mpdus=False, station_id="", binary=None,
                        kind="wsl", wsl_distro="",
                        scan=False, scan_from=400.0, scan_to=2900.0, scan_step=200.0,
                        source="audio", vfo_khz=None, rf_freqs_khz=None):
    """构造 `dumphfdl --iq-file - ...` 命令。

    centerfreq 设为 0，频道频率 = 音频频率(kHz)，这样信道化器把该音频频率
    搬到 0 Hz。

    scan=True 时，在 [scan_from, scan_to] 内按 scan_step 生成一组频道，
    等效于 PC-HFDL / KG-HFDL 的「带内自动搜索」——不必预先知道 HFDL 信号
    落在音频的哪个位置。dumphfdl 会为每个频道独立解码，命中者即可解出。
    """
    from openhfdl_client import _wsl_distros, to_wsl_path
    cfg = cfg
    st = system_table or (cfg.oh_system_table if cfg else "") or presets.default_systable_path()

    if source == "sdr-iq":
        # 真正的复基带 I/Q：centerfreq = VFO(kHz)，频道 = HFDL 射频频率(kHz)。
        # dumphfdl 会用 freq_shift = centerfreq - (freq + 1440Hz) 自行对齐。
        ctr = f"{float(vfo_khz if vfo_khz is not None else 0.0):g}"
        chans = [f"{float(f):g}" for f in (rf_freqs_khz or [])]
        if not chans:
            chans = ["8927"]
    else:
        # 声卡实音频：取 centerfreq = 1440Hz 后，频道频率 == 音频频率。
        ctr = f"{HFDL_SSB_CARRIER_OFFSET_HZ / 1000.0:g}"
        if scan:
            chans, f = [], float(scan_from)
            step = max(50.0, float(scan_step))
            while f <= float(scan_to) + 1e-6:
                chans.append(f"{f / 1000.0:g}")
                f += step
            if not chans:
                chans = [f"{float(audio_center_hz) / 1000.0:g}"]
        else:
            chans = [f"{float(audio_center_hz) / 1000.0:g}"]

    args = ["--iq-file", "-",
            "--sample-format", "CS16",
            "--sample-rate", str(int(soundcard_rate)),
            "--centerfreq", ctr]
    if st and os.path.exists(st):
        args += ["--system-table", to_wsl_path(st) if kind == "wsl" else st]
    args += ["--output", f"decoded:{output_format}:file:path=-"]
    if utc:
        args += ["--utc"]
    if mpdus:
        args += ["--output-mpdus"]
    if station_id:
        args += ["--station-id", station_id]

    args += chans

    exe = binary or "dumphfdl"
    if kind == "wsl":
        distro = wsl_distro or ((_wsl_distros() or ["Ubuntu-20.04"])[0])
        return ["wsl", "-d", distro, "--", exe] + args
    return [exe] + args


# ----------------------------------------------------------------------
# 一体化：启动 dumphfdl 并把声卡数据灌入其 stdin
# ----------------------------------------------------------------------
class BridgedDecoder:
    """桥接解码：声卡 → CS16 I/Q → dumphfdl stdin → 解析 stdout。"""

    def __init__(self, cfg, on_frame=None, on_log=None, mode=MODE_ANALYTIC,
                 audio_center_hz=1370.0, soundcard_rate=48000, device_index=-1,
                 binary=None, kind="wsl", wsl_distro="",
                 scan=False, scan_from=400.0, scan_to=2900.0, scan_step=200.0,
                 exclusive=False, source=None, vfo_khz=None, rf_freqs_khz=None):
        self.cfg = cfg
        self.on_frame = on_frame or (lambda fr: None)
        self.log = on_log or (lambda s: None)
        self.mode = mode
        self.audio_center_hz = float(audio_center_hz)
        self.soundcard_rate = int(soundcard_rate)
        self.device_index = device_index
        self.kind = kind
        self.binary = binary or "dumphfdl"
        self.wsl_distro = wsl_distro
        self.scan = bool(scan)
        self.scan_from = float(scan_from)
        self.scan_to = float(scan_to)
        self.scan_step = float(scan_step)
        self.exclusive = bool(exclusive)
        # 信号来源：audio=声卡实音频；sdr-iq=真正的复基带 I/Q（SDRuno IQ OUT 等）
        self.source = source or getattr(cfg, "oh_bridge_source", "audio")
        if self.source == "sdr-iq":
            # 真正的 I/Q 必须用 stereo-iq（L=I, R=Q）直通，不能做希尔伯特
            self.mode = MODE_STEREO_IQ
        self.vfo_khz = float(vfo_khz if vfo_khz is not None
                             else getattr(cfg, "oh_iq_vfo_khz", 0.0))
        self.rf_freqs_khz = rf_freqs_khz or list(getattr(cfg, "oh_freqs", []) or [])
        self.wav_file = None
        self.proc = None
        self.bridge = None
        self.running = threading.Event()
        self._reader = None
        self._seen = {}          # 去重：多频道可能解出同一帧
        self.stats = dict(frames=0, lines=0, errors=0, dupes=0)

    def _is_duplicate(self, rec):
        """扫描模式下多个频道可能解出同一传输，按关键字段 + 秒级时间去重。"""
        key = (rec.get("flight"), rec.get("reg"), rec.get("icao"),
               None if rec.get("lat") is None else round(rec["lat"], 3),
               None if rec.get("lon") is None else round(rec["lon"], 3),
               int(rec.get("ts") or 0))
        now = time.time()
        # 清理 60 秒前的记录
        for k in [k for k, t in self._seen.items() if now - t > 60]:
            self._seen.pop(k, None)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def start(self):
        if self.wav_file:
            return self._start_wav()
        if self.soundcard_rate < MIN_SAMPLE_RATE:
            self.log(f"[bridge] 采样率 {self.soundcard_rate} 低于 "
                     f"{MIN_SAMPLE_RATE}，dumphfdl 会拒绝")
            return False
        cmd = build_stdin_command(
            self.soundcard_rate, self.audio_center_hz, cfg=self.cfg,
            output_format=getattr(self.cfg, "oh_output_format", "json") or "json",
            utc=getattr(self.cfg, "oh_utc", True),
            mpdus=getattr(self.cfg, "oh_output_mpdus", False),
            binary=self.binary, kind=self.kind, wsl_distro=self.wsl_distro,
            scan=self.scan, scan_from=self.scan_from, scan_to=self.scan_to,
            scan_step=self.scan_step,
            source=self.source, vfo_khz=self.vfo_khz,
            rf_freqs_khz=self.rf_freqs_khz)
        self.log("[bridge] " + " ".join(cmd))

        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0)
        except Exception as e:
            self.log(f"[bridge] 启动 dumphfdl 失败: {e}")
            return False

        self.running.set()
        # dumphfdl stdout 解析线程
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        # 声卡 → stdin
        try:
            self.bridge = SoundcardBridge(
                device_index=self.device_index, sample_rate=self.soundcard_rate,
                mode=self.mode, sink=self.proc.stdin, log=self.log,
                exclusive=self.exclusive)
            self.bridge.start()
        except Exception as e:
            self.log(f"[bridge] 打开声卡失败: {e}")
            self.stop()
            return False
        return True

    def _read_output(self):
        import openhfdl_client as ohc
        try:
            for line in self.proc.stdout:
                line = line.decode("utf-8", "ignore").rstrip("\r\n") \
                    if isinstance(line, bytes) else line.rstrip("\r\n")
                if not line:
                    continue
                self.stats["lines"] += 1
                if line.lstrip().startswith("{"):
                    rec = ohc.parse_openhfdl_json(line)
                    if rec:
                        if self.scan and self._is_duplicate(rec):
                            self.stats["dupes"] += 1
                            continue
                        self.stats["frames"] += 1
                        fr = ohc.record_to_frame(rec, line)
                        if fr is not None:
                            self.on_frame(fr)
                    else:
                        self.stats["errors"] += 1
                else:
                    self.log("[dumphfdl] " + line[:200])
        except Exception as e:
            self.log(f"[bridge] 读取输出异常: {e}")
        finally:
            self.log("[bridge] dumphfdl 输出结束")

    def stop(self):
        self.running.clear()
        if self.bridge:
            try:
                self.bridge.stop()
            except Exception:
                pass
            self.bridge = None
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
        self.log("[bridge] 已停止")

    # ------------------------------------------------------------------
    # WAV 离线回放（用于 A/B 对比：同一段音频分别喂 PC-HFDL 与本桥接）
    # ------------------------------------------------------------------
    def _start_wav(self):
        try:
            a, rate, ch, fmt = read_wav_any(self.wav_file)
        except Exception as e:
            self.log(f"[bridge] 打开 WAV 失败: {e}")
            return False
        if rate < MIN_SAMPLE_RATE:
            self.log(f"[bridge] WAV 采样率 {rate} 低于 {MIN_SAMPLE_RATE}")
            return False
        self.log(f"[bridge] WAV 格式: {fmt}, {ch}ch, {rate}Hz")
        self.soundcard_rate = int(rate)
        self.log(f"[bridge] WAV 回放: {self.wav_file} {rate}Hz {ch}ch "
                 f"{len(a)/rate:.1f}s")
        # 用 WAV 的实际采样率构造命令
        cmd = build_stdin_command(
            rate, self.audio_center_hz, cfg=self.cfg,
            output_format=getattr(self.cfg, "oh_output_format", "json") or "json",
            utc=getattr(self.cfg, "oh_utc", True),
            mpdus=getattr(self.cfg, "oh_output_mpdus", False),
            binary=self.binary, kind=self.kind, wsl_distro=self.wsl_distro,
            scan=self.scan, scan_from=self.scan_from, scan_to=self.scan_to,
            scan_step=self.scan_step,
            source=self.source, vfo_khz=self.vfo_khz,
            rf_freqs_khz=self.rf_freqs_khz)
        self.log("[bridge] " + " ".join(cmd))
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, bufsize=0)
        except Exception as e:
            self.log(f"[bridge] 启动 dumphfdl 失败: {e}")
            return False
        self.running.set()
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

        def pump():
            self.bridge = SoundcardBridge(mode=self.mode, sample_rate=rate,
                                          sink=self.proc.stdin, log=self.log)
            bs = 4096
            for k in range(0, len(a), bs):
                if not self.running.is_set():
                    break
                self.bridge.feed(a[k:k + bs])
            try:
                if self.proc and self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            self.log(f"[bridge] WAV 灌入完成: {self.bridge.stats['samples']} 采样")

        threading.Thread(target=pump, daemon=True).start()
        return True


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _list_devices():
    from audio import AudioSource
    devs = AudioSource.list_input_devices()
    print(f"输入设备 ({len(devs)}):")
    for i, name, sr in devs:
        print(f"  [{i:2d}] {name}  (默认 {sr:.0f} Hz)")


def main():
    ap = argparse.ArgumentParser(
        description="声卡 → OpenHFDL (dumphfdl) 桥接解码")
    ap.add_argument("--list", action="store_true", help="列出输入设备")
    ap.add_argument("--device", type=int, default=-1, help="输入设备索引")
    ap.add_argument("--rate", type=int, default=48000, help="声卡采样率 (≥5400)")
    ap.add_argument("--mode", choices=MODES, default=MODE_ANALYTIC,
                    help="输入模式")
    ap.add_argument("--audio-center", type=float, default=1370.0,
                    help="HFDL 音频中心频率 Hz（默认 1370）")
    ap.add_argument("--wav", metavar="FILE",
                    help="离线回放 WAV 文件（不占用声卡，便于 A/B 对比）")
    ap.add_argument("--no-scan", action="store_true",
                    help="关闭带内自动搜索，只用 --audio-center 单一频道")
    ap.add_argument("--scan-from", type=float, default=400.0)
    ap.add_argument("--scan-to", type=float, default=2900.0)
    ap.add_argument("--list-devices-only", action="store_true")
    args = ap.parse_args()

    if args.list or args.list_devices_only:
        _list_devices()
        return 0

    from config import HfdlConfig
    import openhfdl_client as ohc

    cfg = HfdlConfig()
    kind, path = ohc.find_dumphfdl()
    if not kind:
        print("未找到 dumphfdl，请先构建（build_openhfdl_wsl.sh）")
        return 1

    print(f"模式: {MODE_LABELS[args.mode]}")
    print(f"声卡: dev={args.device} {args.rate} Hz  音频中心={args.audio_center} Hz")
    print(f"dumphfdl: {kind} {path}")
    print("按 Ctrl-C 停止\n")

    dec = BridgedDecoder(cfg, on_log=lambda s: print(s, flush=True),
                         mode=args.mode, audio_center_hz=args.audio_center,
                         soundcard_rate=args.rate, device_index=args.device,
                         binary=path if kind == "wsl" else path, kind=kind,
                         scan=not args.no_scan,
                         scan_from=args.scan_from, scan_to=args.scan_to)
    if args.wav:
        dec.wav_file = args.wav

    def on_frame(fr):
        pos = f"{fr.lat:.3f},{fr.lon:.3f}" if fr.position_ok() else "—"
        print(f"[FRAME] {fr.flight or '?'}/{fr.reg or '?'} {pos} {fr.text[:60]}",
              flush=True)

    dec.on_frame = on_frame
    if not dec.start():
        return 1
    try:
        while dec.running.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        dec.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
