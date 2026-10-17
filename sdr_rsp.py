# -*- coding: utf-8 -*-
"""
SDRplay RSP 直采 I/Q 支持 —— ctypes 绑定官方 SDRplay API。

为什么不用 SoapySDR
------------------
RSP 系列**没有纯开源的驱动**：硬件必须经由 SDRplay 官方的闭源 API 服务
（Windows 下是 `SDRplayAPIService` + `sdrplay_api.dll`）。开源方案
（SoapySDRPlay3 / gr-sdrplay3）本质都是这个闭源 API 的开源**封装**，
且需要额外安装 SoapySDR 运行时。

本模块直接用 ctypes 调 `sdrplay_api.dll`：
  · 免装 SoapySDR，零额外依赖
  · 直接拿到 int16 I/Q，转成 CS16 交织数据喂给 dumphfdl 的 `--iq-file -`
  · 可在运行中调频率 / 增益（走 sdrplay_api_Update）

数据流
------
    RSP 硬件 → sdrplay_api 回调(int16 I/Q) → CS16 交织 → dumphfdl stdin
                                                  → 解出 HFDL JSON → 地图

关于「调制模式」
---------------
SDR 直采是**基带 I/Q**，不存在 AM/FM/SSB 这类解调模式 —— 解调由下游完成。
本模块因此提供 RSP 真正有的两类「模式」：
  · **IF 模式**：Zero-IF / 450 kHz / 1620 kHz / 2048 kHz（下变频方式）
  · **AGC 模式**：关闭 / 100 Hz / 50 Hz / 5 Hz
HFDL 的 300/600/1200/1800 bps、BPSK/QPSK/8PSK 由 dumphfdl 自动识别，无需选择。
"""

import ctypes as C
import os
import queue
import threading
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, Union, c_char, c_char_p,
                    c_double, c_float, c_int, c_short, c_ubyte, c_uint,
                    c_ushort, c_void_p)

SDRPLAY_MAX_DEVICES = 16
SDRPLAY_MAX_SER_NO_LEN = 64

# 硬件版本 -> 名称
HW_NAMES = {
    1: "RSP1", 255: "RSP1A", 2: "RSP2", 3: "RSPduo",
    4: "RSPdx", 6: "RSP1B", 7: "RSPdx R2",
}

# 错误码 -> 含义
ERR_NAMES = {
    0: "成功", 1: "失败", 2: "参数无效", 3: "超出范围", 4: "增益更新失败",
    5: "射频更新失败", 6: "采样率更新失败", 7: "硬件错误", 8: "混叠错误",
    9: "已初始化", 10: "未初始化", 11: "未启用", 12: "硬件版本错误",
    13: "内存不足", 14: "服务未响应（SDRplayAPIService 未运行？）",
    15: "启动中", 16: "停止中", 17: "模式无效",
    18: "校验失败1", 19: "校验失败2", 20: "校验失败3", 21: "校验失败4",
    22: "校验失败5", 23: "校验失败6", 24: "服务版本无效",
}

# 带宽枚举（值与 API 一致，单位 kHz）
BW_OPTIONS = [200, 300, 600, 1536, 5000, 6000, 7000, 8000]
# IF 模式枚举（值与 API 一致，单位 kHz；0 = Zero-IF）
IF_OPTIONS = [0, 450, 1620, 2048]
# AGC 枚举
AGC_OPTIONS = {"关闭": 0, "100 Hz": 1, "50 Hz": 2, "5 Hz": 3}
# 采样率（Hz）
FS_OPTIONS = [1536000, 2000000, 5000000, 6000000, 8000000]

# sdrplay_api_Update 的 reason 标志
UPD_DEV_FS = 0x00000001
UPD_TUNER_GR = 0x00008000
UPD_TUNER_FRF = 0x00020000
UPD_TUNER_BW = 0x00040000
UPD_TUNER_IF = 0x00080000
UPD_CTRL_AGC = 0x01000000
UPD_CTRL_DECIMATION = 0x00800000
# 抽取倍数（API 只接受这几个值）
DECIM_OPTIONS = [1, 2, 4, 8, 16, 32]
UPD_RSP1A_BIAS = 0x00000010
UPD_RSP2_ANTENNA = 0x00000200
UPD_RSP2_BIAS = 0x00000080
UPD_RSPDX_ANTENNA_EXT1 = 0x00000004
UPD_RSPDX_BIAS_EXT1 = 0x00000002
UPD_NONE_EXT1 = 0

TUNER_A = 1

# DLL 搜索路径
DLL_CANDIDATES = [
    os.environ.get("SDRPLAY_API_DLL", ""),
    r"D:\HAM\SDRplay\API\x64\sdrplay_api.dll",
    r"C:\Program Files\SDRplay\API\x64\sdrplay_api.dll",
    r"C:\Program Files (x86)\SDRplay\API\x64\sdrplay_api.dll",
    r"C:\Program Files\SDRplay\API\sdrplay_api.dll",
    "sdrplay_api.dll",
]


# ======================================================================
# ctypes 结构体（严格对应 API 头文件 v3.15）
# ======================================================================
class DeviceT(Structure):
    _fields_ = [("SerNo", c_char * SDRPLAY_MAX_SER_NO_LEN),
                ("hwVer", c_ubyte),
                ("tuner", c_int),
                ("rspDuoMode", c_int),
                ("valid", c_ubyte),
                ("rspDuoSampleFreq", c_double),
                ("dev", c_void_p)]


class FsFreqT(Structure):
    _fields_ = [("fsHz", c_double), ("syncUpdate", c_ubyte), ("reCal", c_ubyte)]


class SyncUpdateT(Structure):
    _fields_ = [("sampleNum", c_uint), ("period", c_uint)]


class ResetFlagsT(Structure):
    _fields_ = [("resetGainUpdate", c_ubyte), ("resetRfUpdate", c_ubyte),
                ("resetFsUpdate", c_ubyte)]


class Rsp1aParamsT(Structure):
    _fields_ = [("rfNotchEnable", c_ubyte), ("rfDabNotchEnable", c_ubyte)]


class Rsp2ParamsT(Structure):
    _fields_ = [("extRefOutputEn", c_ubyte)]


class RspDuoParamsT(Structure):
    _fields_ = [("extRefOutputEn", c_int)]


class RspDxParamsT(Structure):
    _fields_ = [("hdrEnable", c_ubyte), ("biasTEnable", c_ubyte),
                ("antennaSel", c_int), ("rfNotchEnable", c_ubyte),
                ("rfDabNotchEnable", c_ubyte)]


class DevParamsT(Structure):
    _fields_ = [("ppm", c_double), ("fsFreq", FsFreqT),
                ("syncUpdate", SyncUpdateT), ("resetFlags", ResetFlagsT),
                ("mode", c_int), ("samplesPerPkt", c_uint),
                ("rsp1aParams", Rsp1aParamsT), ("rsp2Params", Rsp2ParamsT),
                ("rspDuoParams", RspDuoParamsT), ("rspDxParams", RspDxParamsT)]


class GainValuesT(Structure):
    _fields_ = [("curr", c_float), ("max", c_float), ("min", c_float)]


class GainT(Structure):
    _fields_ = [("gRdB", c_int), ("LNAstate", c_ubyte),
                ("syncUpdate", c_ubyte), ("minGr", c_int),
                ("gainVals", GainValuesT)]


class RfFreqT(Structure):
    _fields_ = [("rfHz", c_double), ("syncUpdate", c_ubyte)]


class DcOffsetTunerT(Structure):
    _fields_ = [("dcCal", c_ubyte), ("speedUp", c_ubyte),
                ("trackTime", c_int), ("refreshRateTime", c_int)]


class TunerParamsT(Structure):
    _fields_ = [("bwType", c_int), ("ifType", c_int), ("loMode", c_int),
                ("gain", GainT), ("rfFreq", RfFreqT),
                ("dcOffsetTuner", DcOffsetTunerT)]


class DcOffsetT(Structure):
    _fields_ = [("DCenable", c_ubyte), ("IQenable", c_ubyte)]


class DecimationT(Structure):
    _fields_ = [("enable", c_ubyte), ("decimationFactor", c_ubyte),
                ("wideBandSignal", c_ubyte)]


class AgcT(Structure):
    """对应 C 的 sdrplay_api_AgcT：
    int enable; int setPoint_dBfs; unsigned short attack_ms / decay_ms /
    decay_delay_ms / decay_threshold_dB; int syncUpdate  → 20 字节
    """
    _fields_ = [("enable", c_int), ("setPoint_dBfs", c_int),
                ("attack_ms", c_ushort), ("decay_ms", c_ushort),
                ("decay_delay_ms", c_ushort),
                ("decay_threshold_dB", c_ushort), ("syncUpdate", c_int)]


class ControlParamsT(Structure):
    _fields_ = [("dcOffset", DcOffsetT), ("decimation", DecimationT),
                ("agc", AgcT), ("adsbMode", c_int)]


class Rsp1aTunerParamsT(Structure):
    _fields_ = [("biasTEnable", c_ubyte)]


class Rsp2TunerParamsT(Structure):
    _fields_ = [("biasTEnable", c_ubyte), ("amPortSel", c_int),
                ("antennaSel", c_int), ("rfNotchEnable", c_ubyte)]


class RspDuoResetSlaveFlagsT(Structure):
    _fields_ = [("resetGainUpdate", c_ubyte), ("resetRfUpdate", c_ubyte)]


class RspDuoTunerParamsT(Structure):
    _fields_ = [("biasTEnable", c_ubyte), ("tuner1AmPortSel", c_int),
                ("tuner1AmNotchEnable", c_ubyte), ("rfNotchEnable", c_ubyte),
                ("rfDabNotchEnable", c_ubyte),
                ("resetSlaveFlags", RspDuoResetSlaveFlagsT)]


class RspDxTunerParamsT(Structure):
    _fields_ = [("hdrBw", c_int)]


class RxChannelParamsT(Structure):
    _fields_ = [("tunerParams", TunerParamsT), ("ctrlParams", ControlParamsT),
                ("rsp1aTunerParams", Rsp1aTunerParamsT),
                ("rsp2TunerParams", Rsp2TunerParamsT),
                ("rspDuoTunerParams", RspDuoTunerParamsT),
                ("rspDxTunerParams", RspDxTunerParamsT)]


class DeviceParamsT(Structure):
    _fields_ = [("devParams", POINTER(DevParamsT)),
                ("rxChannelA", POINTER(RxChannelParamsT)),
                ("rxChannelB", POINTER(RxChannelParamsT))]


class StreamCbParamsT(Structure):
    _fields_ = [("firstSampleNum", c_uint), ("grChanged", c_int),
                ("rfChanged", c_int), ("fsChanged", c_int),
                ("numSamples", c_uint)]


class GainCbParamT(Structure):
    _fields_ = [("gRdB", c_uint), ("lnaGRdB", c_uint), ("currGain", c_double)]


class PowerOverloadCbParamT(Structure):
    _fields_ = [("powerOverloadChangeType", c_int)]


class RspDuoModeCbParamT(Structure):
    _fields_ = [("modeChangeType", c_int)]


class EventParamsT(Union):
    _fields_ = [("gainParams", GainCbParamT),
                ("powerOverloadParams", PowerOverloadCbParamT),
                ("rspDuoModeParams", RspDuoModeCbParamT)]


class ErrorInfoT(Structure):
    """对应 C 的 sdrplay_api_ErrorInfoT：定位 API 内部出错位置。"""
    _fields_ = [("file", c_char * 256), ("function", c_char * 256),
                ("line", c_int), ("message", c_char * 1024)]


STREAM_CB = CFUNCTYPE(None, POINTER(c_short), POINTER(c_short),
                      POINTER(StreamCbParamsT), c_uint, c_uint, c_void_p)
EVENT_CB = CFUNCTYPE(None, c_int, c_int, POINTER(EventParamsT), c_void_p)


class CallbackFnsT(Structure):
    _fields_ = [("StreamACbFn", STREAM_CB), ("StreamBCbFn", STREAM_CB),
                ("EventCbFn", EVENT_CB)]


# ======================================================================
# DLL 加载与函数签名
# ======================================================================
_lib = None
_lib_error = ""


def find_dll():
    for p in DLL_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def load_api():
    """加载 sdrplay_api.dll 并绑定函数签名（幂等）。"""
    global _lib, _lib_error
    if _lib is not None:
        return _lib
    path = find_dll()
    if not path:
        _lib_error = ("未找到 sdrplay_api.dll —— 请先安装 SDRplay API。\n"
                      "已查找: " + ", ".join(p for p in DLL_CANDIDATES if p))
        return None
    try:
        lib = C.WinDLL(path) if hasattr(C, "WinDLL") else C.CDLL(path)
    except Exception as e:
        _lib_error = f"加载 {path} 失败: {e}"
        return None
    try:
        lib.sdrplay_api_Open.restype = c_int
        lib.sdrplay_api_Close.restype = c_int
        lib.sdrplay_api_ApiVersion.restype = c_int
        lib.sdrplay_api_ApiVersion.argtypes = [POINTER(c_float)]
        lib.sdrplay_api_LockDeviceApi.restype = c_int
        lib.sdrplay_api_UnlockDeviceApi.restype = c_int
        lib.sdrplay_api_GetDevices.restype = c_int
        lib.sdrplay_api_GetDevices.argtypes = [POINTER(DeviceT),
                                               POINTER(c_uint), c_uint]
        lib.sdrplay_api_SelectDevice.restype = c_int
        lib.sdrplay_api_SelectDevice.argtypes = [POINTER(DeviceT)]
        lib.sdrplay_api_ReleaseDevice.restype = c_int
        lib.sdrplay_api_ReleaseDevice.argtypes = [POINTER(DeviceT)]
        lib.sdrplay_api_GetErrorString.restype = c_char_p
        lib.sdrplay_api_GetErrorString.argtypes = [c_int]
        lib.sdrplay_api_GetDeviceParams.restype = c_int
        lib.sdrplay_api_GetDeviceParams.argtypes = [c_void_p,
                                                    POINTER(POINTER(DeviceParamsT))]
        lib.sdrplay_api_Init.restype = c_int
        lib.sdrplay_api_Init.argtypes = [c_void_p, POINTER(CallbackFnsT),
                                         c_void_p]
        lib.sdrplay_api_Uninit.restype = c_int
        lib.sdrplay_api_Uninit.argtypes = [c_void_p]
        lib.sdrplay_api_Update.restype = c_int
        lib.sdrplay_api_Update.argtypes = [c_void_p, c_int, c_int, c_int]
        lib.sdrplay_api_DebugEnable.restype = c_int
        lib.sdrplay_api_DebugEnable.argtypes = [c_void_p, c_int]
        lib.sdrplay_api_GetLastError.restype = POINTER(ErrorInfoT)
        lib.sdrplay_api_GetLastError.argtypes = [POINTER(DeviceT)]
    except AttributeError as e:
        _lib_error = f"{path} 缺少预期符号: {e}"
        return None
    _lib = lib
    _lib_error = ""
    return _lib


def err_str(code):
    lib = _lib
    if lib is not None:
        try:
            s = lib.sdrplay_api_GetErrorString(code)
            if s:
                return f"{code}: {s.decode('utf-8', 'ignore')}"
        except Exception:
            pass
    return f"{code}: {ERR_NAMES.get(code, '未知错误')}"


def last_error(dev):
    """读取 API 最近一次错误的位置信息（文件/函数/行号/说明）。"""
    if _lib is None or dev is None:
        return ""
    try:
        p = _lib.sdrplay_api_GetLastError(C.byref(dev))
        if not p:
            return ""
        e = p.contents
        f = e.file.decode("utf-8", "ignore").rstrip("\x00")
        fn = e.function.decode("utf-8", "ignore").rstrip("\x00")
        msg = e.message.decode("utf-8", "ignore").rstrip("\x00")
        return f"{fn or '?'} @{f.split(chr(92))[-1] if f else '?'}:{e.line} —— {msg}"
    except Exception:
        return ""


def api_info():
    """返回 (是否可用, 版本字符串/错误说明)。"""
    lib = load_api()
    if lib is None:
        return False, _lib_error
    rc = lib.sdrplay_api_Open()
    if rc != 0:
        return False, ("sdrplay_api_Open 失败 —— " + err_str(rc) +
                       "\n请确认 SDRplayAPIService 服务已启动。")
    try:
        v = c_float(0.0)
        rc = lib.sdrplay_api_ApiVersion(C.byref(v))
        if rc != 0:
            return False, "sdrplay_api_ApiVersion 失败 —— " + err_str(rc)
        return True, f"SDRplay API {v.value:.2f}"
    finally:
        lib.sdrplay_api_Close()


def list_devices():
    """枚举 RSP 设备。返回 (设备列表, 说明)。

    每项: {"index","serial","hw","hw_name","tuner","valid","sdrplay_available"}
    """
    lib = load_api()
    if lib is None:
        return [], _lib_error
    rc = lib.sdrplay_api_Open()
    if rc != 0:
        return [], ("sdrplay_api_Open 失败 —— " + err_str(rc) +
                    "\n请确认 SDRplayAPIService 服务已启动。")
    try:
        devs = (DeviceT * SDRPLAY_MAX_DEVICES)()
        num = c_uint(0)
        rc = lib.sdrplay_api_GetDevices(devs, C.byref(num),
                                        SDRPLAY_MAX_DEVICES)
        if rc != 0:
            return [], "sdrplay_api_GetDevices 失败 —— " + err_str(rc)
        out = []
        for i in range(int(num.value)):
            d = devs[i]
            out.append({
                "index": i,
                "serial": d.SerNo.decode("utf-8", "ignore").strip("\x00").strip(),
                "hw": int(d.hwVer),
                "hw_name": HW_NAMES.get(int(d.hwVer), f"未知({d.hwVer})"),
                "tuner": int(d.tuner),
                "valid": bool(d.valid) and int(d.tuner) != 0,
            })
        if not out:
            return [], ("未发现 RSP 设备。请检查：\n"
                        "· USB 线是否插好（换一个 USB3 口）\n"
                        "· SDRplayAPIService 服务是否运行\n"
                        "· 是否有其它程序（SDRuno 等）已占用设备")
        return out, ""
    finally:
        lib.sdrplay_api_Close()


# ======================================================================
# 采集源
# ======================================================================
_NP = None
_NP_TRIED = False


def _np():
    """延迟导入 numpy（回调里用不到时不影响模块加载）。"""
    global _NP, _NP_TRIED
    if not _NP_TRIED:
        _NP_TRIED = True
        try:
            import numpy
            _NP = numpy
        except Exception:
            _NP = None
    return _NP


class RspSource:
    """单台 RSP 的 I/Q 采集源。

    on_iq(bytes) 回调收到的是 **CS16 交织** 数据（int16 小端，I,Q,I,Q…），
    正好是 dumphfdl `--sample-format CS16` 要的格式。
    """

    def __init__(self, device_index=0, sample_rate=2000000,
                 freq_khz=8903.0, bw_khz=1536, if_khz=0,
                 agc=2, gain_gr_db=40, lna_state=0,
                 antenna=None, bias_t=False, dc_block=False, decim=1, log=None):
        self.dc_block = bool(dc_block)
        # 零中频 SDR 在本振处有强 DC 尖峰（实测 +30 dB、约 13 kHz 宽）。
        # 简单高通去不掉（比尖峰窄），**正解是让中心频率避开 HFDL 频道**；
        # dc_block 仅作可选项保留。
        self._dc = 0j
        self.decim = int(decim) if int(decim) in DECIM_OPTIONS else 1
        self.device_index = int(device_index)
        self.sample_rate = float(sample_rate)
        self.freq_khz = float(freq_khz)
        self.bw_khz = int(bw_khz)
        self.if_khz = int(if_khz)
        self.agc = int(agc)
        self.gain_gr_db = int(gain_gr_db)
        self.lna_state = int(lna_state)
        self.antenna = antenna
        self.bias_t = bool(bias_t)
        self.log = log or (lambda s: None)

        self._lib = None
        self._dev = None
        self._devs = None
        self._params = None
        self._cbs = None
        self._stream_cb = None
        self._event_cb = None
        self._on_iq = None
        self.running = threading.Event()
        self.stats = {"blocks": 0, "samples": 0, "overloads": 0, "dropped": 0}

    # -- 内部 ------------------------------------------------------
    def _apply_prefs(self):
        """把本对象的参数写进 API 的 DeviceParams（Init 前 / Update 前均可用）。"""
        p = self._params.contents
        dp = p.devParams.contents
        ch = p.rxChannelA.contents
        dp.fsFreq.fsHz = self.sample_rate
        ch.tunerParams.rfFreq.rfHz = self.freq_khz * 1e3
        ch.tunerParams.bwType = self.bw_khz
        ch.tunerParams.ifType = self.if_khz
        ch.tunerParams.gain.gRdB = self.gain_gr_db
        ch.tunerParams.gain.LNAstate = self.lna_state
        ch.tunerParams.gain.minGr = 20          # NORMAL_MIN_GR
        ch.tunerParams.loMode = 1               # LO_Auto
        ch.ctrlParams.dcOffset.DCenable = 1
        ch.ctrlParams.dcOffset.IQenable = 1
        ch.ctrlParams.decimation.enable = 1 if self.decim > 1 else 0
        ch.ctrlParams.decimation.decimationFactor = self.decim
        ch.ctrlParams.decimation.wideBandSignal = 0
        ch.ctrlParams.agc.enable = self.agc
        ch.ctrlParams.agc.setPoint_dBfs = -60
        ch.ctrlParams.agc.syncUpdate = 0
        ch.ctrlParams.adsbMode = 0
        # 天线 / 偏置 T 按型号
        hw = int(self._dev.hwVer)
        if hw == 2:                              # RSP2
            if self.antenna is not None:
                ch.rsp2TunerParams.antennaSel = int(self.antenna)
            ch.rsp2TunerParams.biasTEnable = 1 if self.bias_t else 0
        elif hw in (4, 7):                       # RSPdx / RSPdx R2
            if self.antenna is not None:
                dp.rspDxParams.antennaSel = int(self.antenna)
            dp.rspDxParams.biasTEnable = 1 if self.bias_t else 0
        else:                                    # RSP1/1A/1B/duo
            if self.bias_t:
                ch.rsp1aTunerParams.biasTEnable = 1
                ch.rspDuoTunerParams.biasTEnable = 1

    def _stream_cb_impl(self, xi, xq, params, num_samples, reset, ctx):
        """API 线程回调：必须极快，否则丢样本。

        用 numpy 向量化交织（逐样本 Python 循环在 2 MSPS 下会丢掉 97% 数据）。
        """
        if not self.running.is_set() or num_samples <= 0:
            return
        try:
            n = int(num_samples)
            np_ = _np()
            if np_ is not None:
                iv = np_.ctypeslib.as_array(xi, shape=(n,))
                qv = np_.ctypeslib.as_array(xq, shape=(n,))
                if self.dc_block:
                    # 零中频 SDR 的本振泄漏在 0 Hz 形成强尖峰（实测 +33 dB），
                    # 会淹没落在中心频率上的频道。用长时常数 EMA 估计并扣除 DC，
                    # 等效 ~300 Hz 高通：远窄于 HFDL 频道间隔，不影响正常工作。
                    z = iv.astype(np_.float32) + 1j * qv.astype(np_.float32)
                    m = z.mean()
                    a = 1e-3
                    self._dc += a * (m - self._dc)
                    z -= self._dc
                    iv2 = np_.clip(z.real, -32768, 32767).astype(np_.int16)
                    qv2 = np_.clip(z.imag, -32768, 32767).astype(np_.int16)
                    iv, qv = iv2, qv2
                out = np_.empty(2 * n, dtype=np_.int16)
                out[0::2] = iv
                out[1::2] = qv
                ba = out.tobytes()
            else:                       # numpy 缺失时的兜底
                buf = (c_short * (2 * n))()
                for i in range(n):
                    buf[2 * i] = xi[i]
                    buf[2 * i + 1] = xq[i]
                ba = bytes(memoryview(buf).cast("B"))
            self.stats["blocks"] += 1
            self.stats["samples"] += n
            cb = self._on_iq
            if cb is not None:
                cb(ba)
        except Exception:
            pass

    def _event_cb_impl(self, event_id, tuner, params, ctx):
        # 1 = 过载（power overload），记一次
        try:
            if int(event_id) == 1:
                self.stats["overloads"] += 1
        except Exception:
            pass

    # -- 对外 ------------------------------------------------------
    @property
    def out_sample_rate(self):
        """实际输出采样率（开抽取后 = 采样率 / 抽取倍数）。"""
        return float(self.sample_rate) / float(self.decim or 1)

    def set_decim(self, decim):
        """运行时改抽取倍数（需 Init 之后调用）。"""
        if int(decim) not in DECIM_OPTIONS:
            return False
        self.decim = int(decim)
        if not self._lib or not self._params or not self._dev:
            return False
        ch = self._params.contents.rxChannelA.contents
        ch.ctrlParams.decimation.enable = 1 if self.decim > 1 else 0
        ch.ctrlParams.decimation.decimationFactor = self.decim
        rc = self._lib.sdrplay_api_Update(self._dev.dev, TUNER_A,
                                          UPD_CTRL_DECIMATION, UPD_NONE_EXT1)
        if rc != 0:
            self.log(f"[rsp] 改抽取失败 —— {err_str(rc)}")
            return False
        self.log(f"[rsp] 抽取倍数 → {self.decim}×，"
                 f"输出采样率 {self.out_sample_rate/1e3:.0f} kSPS")
        return True

    def open(self):
        lib = load_api()
        if lib is None:
            self.log("[rsp] " + _lib_error)
            return False
        self._lib = lib
        rc = lib.sdrplay_api_Open()
        if rc != 0:
            self.log(f"[rsp] Open 失败 —— {err_str(rc)}")
            return False
        # 官方推荐顺序：Open → LockDeviceApi → GetDevices → SelectDevice → Unlock
        locked = False
        try:
            locked = (lib.sdrplay_api_LockDeviceApi() == 0)
        except Exception:
            pass
        devs = (DeviceT * SDRPLAY_MAX_DEVICES)()
        num = c_uint(0)
        rc = lib.sdrplay_api_GetDevices(devs, C.byref(num), SDRPLAY_MAX_DEVICES)
        if rc != 0 or int(num.value) == 0:
            self.log(f"[rsp] 未发现设备（{err_str(rc) if rc else '数量 0'}）")
            if locked:
                try:
                    lib.sdrplay_api_UnlockDeviceApi()
                except Exception:
                    pass
            lib.sdrplay_api_Close()
            return False
        self._devs = devs
        idx = min(self.device_index, int(num.value) - 1)
        self._dev = devs[idx]
        rc = lib.sdrplay_api_SelectDevice(C.byref(self._dev))
        if locked:
            try:
                lib.sdrplay_api_UnlockDeviceApi()
            except Exception:
                pass
        if rc != 0:
            self.log(f"[rsp] SelectDevice 失败 —— {err_str(rc)}"
                     f"  {last_error(self._dev)}"
                     "（设备是否被 SDRuno 等程序占用？）")
            lib.sdrplay_api_Close()
            return False
        pp = POINTER(DeviceParamsT)()
        rc = lib.sdrplay_api_GetDeviceParams(self._dev.dev, C.byref(pp))
        if rc != 0:
            self.log(f"[rsp] GetDeviceParams 失败 —— {err_str(rc)}")
            self.close()
            return False
        self._params = pp
        self._apply_prefs()
        dec_txt = (f"  抽取={self.decim}×→输出 {self.out_sample_rate/1e3:.0f} kSPS"
                   if self.decim > 1 else "  抽取=关")
        self.log(f"[rsp] 已选中 {HW_NAMES.get(int(self._dev.hwVer), 'RSP')}"
                 f"  SN={self._dev.SerNo.decode('utf-8','ignore').strip(chr(0))}"
                 f"  采样率={self.sample_rate/1e6:g} MSPS"
                 f"  中心={self.freq_khz:g} kHz  BW={self.bw_khz} kHz"
                 f"  IF={self.if_khz} kHz  AGC={self.agc}{dec_txt}")
        return True

    def start(self, on_iq):
        """开始采集。on_iq(bytes) 在 API 线程被调用（勿做耗时操作）。"""
        if self._lib is None or self._params is None:
            return False
        self._on_iq = on_iq
        self._stream_cb = STREAM_CB(self._stream_cb_impl)
        self._event_cb = EVENT_CB(self._event_cb_impl)
        self._cbs = CallbackFnsT(self._stream_cb, STREAM_CB(), self._event_cb)
        self.running.set()
        rc = self._lib.sdrplay_api_Init(self._dev.dev, C.byref(self._cbs), None)
        if rc != 0:
            detail = last_error(self._dev)
            # LNA 状态越界时自动降级到 0 重试一次（RSP1 在 HF 只有 0..3）
            if "LNAstate" in detail and self.lna_state != 0:
                self.log(f"[rsp] LNA 状态 {self.lna_state} 超出本机范围，"
                         f"自动改为 0 重试")
                self.lna_state = 0
                self._params.contents.rxChannelA.contents \
                    .tunerParams.gain.LNAstate = 0
                rc = self._lib.sdrplay_api_Init(
                    self._dev.dev, C.byref(self._cbs), None)
                if rc == 0:
                    return True
                detail = last_error(self._dev)
            self.running.clear()
            self.log(f"[rsp] Init 失败 —— {err_str(rc)}"
                     + (f"  [{detail}]" if detail else "")
                     + "  （检查采样率/带宽组合，或设备被占用）")
            return False
        return True

    def set_freq_khz(self, khz):
        """运行中调中心频率。"""
        if not self._lib or not self._params or not self._dev:
            self.freq_khz = float(khz)
            return False
        self.freq_khz = float(khz)
        self._params.contents.rxChannelA.contents.tunerParams.rfFreq.rfHz = \
            self.freq_khz * 1e3
        rc = self._lib.sdrplay_api_Update(self._dev.dev, TUNER_A,
                                          UPD_TUNER_FRF, UPD_NONE_EXT1)
        if rc != 0:
            self.log(f"[rsp] 调频失败 —— {err_str(rc)}")
            return False
        self.log(f"[rsp] 已调到 {self.freq_khz:g} kHz")
        return True

    def set_gain(self, gr_db, lna_state=None, agc=None):
        if not self._lib or not self._params or not self._dev:
            return False
        self.gain_gr_db = int(gr_db)
        if lna_state is not None:
            self.lna_state = int(lna_state)
        if agc is not None:
            self.agc = int(agc)
        ch = self._params.contents.rxChannelA.contents
        ch.tunerParams.gain.gRdB = self.gain_gr_db
        ch.tunerParams.gain.LNAstate = self.lna_state
        ch.ctrlParams.agc.enable = self.agc
        rc = self._lib.sdrplay_api_Update(self._dev.dev, TUNER_A,
                                          UPD_TUNER_GR, UPD_NONE_EXT1)
        rc2 = self._lib.sdrplay_api_Update(self._dev.dev, TUNER_A,
                                           UPD_CTRL_AGC, UPD_NONE_EXT1)
        if rc != 0:
            self.log(f"[rsp] 调增益失败 —— {err_str(rc)}")
            return False
        return rc2 == 0

    def stop(self):
        self.running.clear()
        if self._lib and self._dev and self._dev.dev:
            try:
                self._lib.sdrplay_api_Uninit(self._dev.dev)
            except Exception:
                pass

    def close(self):
        self.stop()
        if self._lib and self._dev is not None:
            try:
                self._lib.sdrplay_api_ReleaseDevice(C.byref(self._dev))
            except Exception:
                pass
        if self._lib:
            try:
                self._lib.sdrplay_api_Close()
            except Exception:
                pass
        self._lib = None
        self._dev = None
        self._params = None


# ======================================================================
# 桥接到 dumphfdl stdin
# ======================================================================
class RspBridge:
    """把 RSP 的 I/Q（CS16）持续写入 sink（dumphfdl 的 stdin）。

    接口与 SoundcardBridge 对齐（start / stop），便于复用 BridgedDecoder。
    """

    def __init__(self, sink, log=None, device_index=0, sample_rate=2000000,
                 freq_khz=8927.0, bw_khz=1536, if_khz=0, agc=2,
                 gain_gr_db=40, lna_state=4, antenna=None, bias_t=False,
                 decim=1, queue_blocks=2048):
        self.sink = sink
        self.log = log or (lambda s: None)
        self.queue_blocks = int(queue_blocks)
        self.q = queue.Queue(maxsize=self.queue_blocks)
        self.running = threading.Event()
        self.writer = None
        self.stats = {"written": 0, "dropped": 0}
        self.source = RspSource(
            device_index=device_index, sample_rate=sample_rate,
            freq_khz=freq_khz, bw_khz=bw_khz, if_khz=if_khz, agc=agc,
            gain_gr_db=gain_gr_db, lna_state=lna_state, antenna=antenna,
            bias_t=bias_t, decim=decim, log=self.log)

    @property
    def out_rate(self):
        """喂给 dumphfdl 的实际采样率（含抽取）。"""
        return self.source.out_sample_rate

    # API 线程里只入队，绝不阻塞
    def _on_iq(self, ba):
        """入队。队列满时**丢弃最旧的块**而不是丢掉新块。

        直播解码时"新数据"比"旧数据"更有价值：丢掉最旧的一段只造成一次时间
        空洞，而丢新块会让解码延迟越来越大。溢出通常只发生在启动瞬间
        （dumphfdl 还在初始化、尚未开始读 stdin），见 RspDecoder 的就绪等待。
        """
        try:
            self.q.put_nowait(ba)
        except queue.Full:
            try:
                self.q.get_nowait()          # 丢最旧
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(ba)
            except queue.Full:
                pass
            self.stats["dropped"] += 1

    def _write_loop(self):
        """把队列里的块合并后大块写入，减少系统调用（2 MSPS 下很关键）。"""
        pending = []
        pend_bytes = 0
        flush_at = 65536                      # 每次写 ≥64 KB
        last_warn = 0.0
        while self.running.is_set():
            try:
                ba = self.q.get(timeout=0.2)
                pending.append(ba)
                pend_bytes += len(ba)
                # 顺带把队列里已到的块一次取空
                while True:
                    try:
                        b2 = self.q.get_nowait()
                    except queue.Empty:
                        break
                    pending.append(b2)
                    pend_bytes += len(b2)
            except queue.Empty:
                pass
            if pending and (pend_bytes >= flush_at or not self.running.is_set()
                            or self.q.empty()):
                try:
                    self.sink.write(b"".join(pending))
                except Exception as e:
                    self.log(f"[rsp] 写入 dumphfdl 失败: {e}")
                    break
                self.stats["written"] += len(pending)
                pending, pend_bytes = [], 0
            # 丢块告警（最多每 5 秒一次），并给出针对性建议
            if self.stats["dropped"] and time.time() - last_warn > 5.0:
                last_warn = time.time()
                rate_mbps = (self.source.out_sample_rate * 4) / 1e6
                hint = ("建议提高抽取倍数（硬件抽取可 1/2/4/8…）"
                        if self.source.decim < 16 else "建议降低采样率")
                self.log(f"[rsp] 队列溢出，累计丢 {self.stats['dropped']} 块"
                         f"（输出 {self.source.out_sample_rate/1e3:.0f} kSPS"
                         f"≈{rate_mbps:.2f} MB/s，抽取 {self.source.decim}×）"
                         f"—— {hint}")
        if pending:
            try:
                self.sink.write(b"".join(pending))
                self.stats["written"] += len(pending)
            except Exception:
                pass

    def start(self):
        if not self.source.open():
            return False
        self.running.set()
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()
        if not self.source.start(self._on_iq):
            self.running.clear()
            self.source.close()
            return False
        self.log("[rsp] 采集已启动 → dumphfdl stdin")
        return True

    def stop(self):
        self.running.clear()
        try:
            self.source.close()
        except Exception:
            pass
        if self.writer is not None:
            self.writer.join(timeout=2)
            self.writer = None
        self.log(f"[rsp] 采集停止（写入 {self.stats['written']} 块，"
                 f"丢弃 {self.stats['dropped']} 块，"
                 f"过载 {self.source.stats['overloads']} 次）")


class RspDecoder:
    """RSP 直采 → dumphfdl → JSON 解析（复用 soundcard_bridge 的解析逻辑）。"""

    def __init__(self, cfg, on_frame=None, on_log=None, **kw):
        import soundcard_bridge as scb
        self._scb = scb
        self.cfg = cfg
        self.on_frame = on_frame
        self.log = on_log or (lambda s: None)
        self.kw = kw
        self.proc = None
        self.reader = None
        self.bridge = None
        self.running = threading.Event()
        self._ready = threading.Event()      # dumphfdl 初始化完成、可以喂数据
        self.stats = {"lines": 0, "frames": 0, "errors": 0}

    def start(self):
        scb = self._scb
        fs = float(self.kw.get("sample_rate", 2000000))
        fc = float(self.kw.get("freq_khz", 8903.0))
        # 开硬件抽取后，喂给 dumphfdl 的是抽取后的采样率，频道距离限制也随之收紧
        decim = int(self.kw.get("decim", 1) or 1)
        fs_eff = fs / float(decim)
        # dumphfdl 要求频道与中心频率距离 ≤ 采样率的一半；超出会被拒绝退出。
        # 这里主动过滤并明确报错，绝不静默回退到默认频道（那会让 dumphfdl 直接退出）。
        raw_chans = self.kw.get("channels") or []
        half = fs_eff / 2.0 / 1e3
        chans = [f for f in raw_chans if abs(float(f) - fc) <= half - 1.0]
        dropped = [f for f in raw_chans if f not in chans]
        if not chans:
            self.log(f"[rsp] 没有任何频道落在 {fc-half:.0f}–{fc+half:.0f} kHz 内"
                     f"（中心 {fc:g} kHz，输出采样率 {fs_eff/1e6:g} MSPS"
                     f"{f'，抽取 {decim}×' if decim > 1 else ''}）。"
                     f"请调整中心频率、抽取倍数或换用对应波段的频道预设。")
            return False
        if dropped:
            self.log(f"[rsp] 忽略 {len(dropped)} 个超出输出带宽的频道: "
                     + ", ".join(f"{float(f):g}" for f in dropped[:8])
                     + (" …" if len(dropped) > 8 else ""))
        cmd = scb.build_stdin_command(
            fs_eff, fc * 1e3,
            cfg=self.cfg,
            output_format=getattr(self.cfg, "oh_output_format", "json") or "json",
            utc=getattr(self.cfg, "oh_utc", True),
            mpdus=getattr(self.cfg, "oh_output_mpdus", False),
            binary=self.kw.get("binary"), kind=self.kw.get("kind", "wsl"),
            wsl_distro=self.kw.get("wsl_distro", ""),
            source="sdr-iq",
            vfo_khz=fc,
            rf_freqs_khz=chans)
        self.log("[rsp] " + " ".join(cmd))
        import subprocess
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0)
        except Exception as e:
            self.log(f"[rsp] 启动 dumphfdl 失败: {e}")
            return False
        self.running.set()
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()
        # 关键：dumphfdl 要先加载系统表、起 FFT 线程（约 1–2 秒）才开始读 stdin。
        # 若此时 RSP 已满速推流，管道与队列瞬间塞满 → 队列溢出丢块。
        # 因此等它就绪信号（或超时）再启动采集。
        t_wait = time.time()
        got_ready = self._ready.wait(timeout=6.0)
        waited = time.time() - t_wait
        if got_ready:
            self.log(f"[rsp] dumphfdl 已就绪（等待 {waited*1000:.0f} ms），开始采集")
        else:
            self.log(f"[rsp] 等待 dumphfdl 就绪超时（{waited:.1f}s），仍继续采集")
        self.bridge = RspBridge(
            sink=self.proc.stdin, log=self.log,
            device_index=self.kw.get("device_index", 0),
            sample_rate=self.kw.get("sample_rate", 2000000),
            freq_khz=self.kw.get("freq_khz", 8927.0),
            bw_khz=self.kw.get("bw_khz", 1536),
            if_khz=self.kw.get("if_khz", 0),
            agc=self.kw.get("agc", 2),
            gain_gr_db=self.kw.get("gain_gr_db", 40),
            lna_state=self.kw.get("lna_state", 4),
            antenna=self.kw.get("antenna"),
            bias_t=self.kw.get("bias_t", False),
            decim=decim)
        if not self.bridge.start():
            self.log("[rsp] 采集启动失败")
            self.stop()
            return False
        return True

    def _read_output(self):
        import openhfdl_client as ohc
        try:
            for line in self.proc.stdout:
                line = (line.decode("utf-8", "ignore").rstrip("\r\n")
                        if isinstance(line, bytes) else line.rstrip("\r\n"))
                if not line:
                    continue
                self.stats["lines"] += 1
                if line.lstrip().startswith("{"):
                    rec = ohc.parse_openhfdl_json(line)
                    if rec:
                        self.stats["frames"] += 1
                        fr = ohc.record_to_frame(rec, line)
                        if fr is not None and self.on_frame:
                            self.on_frame(fr)
                    else:
                        self.stats["errors"] += 1
                else:
                    self.log("[dumphfdl] " + line[:200])
                    # 就绪标志：dumphfdl 打印完初始化横幅后才会去读 stdin。
                    # 出现 "Initialized ... threads"（FFT）即表示初始化接近完成；
                    # 退一步，见到第 3 行日志也认为可开始喂数据。
                    low = line.lower()
                    if ("initialized" in low or "fft threads" in low
                            or self.stats["lines"] >= 3):
                        self._ready.set()
        except Exception as e:
            self.log(f"[rsp] 读取输出异常: {e}")
        finally:
            self.log("[rsp] dumphfdl 输出结束")

    def set_freq_khz(self, khz):
        if self.bridge is not None:
            return self.bridge.source.set_freq_khz(khz)
        return False

    def stop(self):
        self.running.clear()
        if self.bridge is not None:
            try:
                self.bridge.stop()
            except Exception:
                pass
            self.bridge = None
        if self.proc is not None:
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
        self.log("[rsp] 已停止")


def selftest():
    """无硬件也能跑的自检：DLL 加载、API 版本、设备枚举、结构体大小。"""
    print("=" * 62)
    print("SDRplay API 自检")
    print("=" * 62)
    dll = find_dll()
    print(f"  DLL: {dll or '未找到'}")
    if dll:
        ok, msg = api_info()
        print(f"  API: {'OK  ' if ok else 'FAIL'} {msg}")
    devs, note = list_devices()
    print(f"  设备: {len(devs)} 台")
    for d in devs:
        print(f"    [{d['index']}] {d['hw_name']}  SN={d['serial']}  "
              f"tuner={d['tuner']} valid={d['valid']}")
    if note:
        print("        " + note.replace("\n", "\n        "))
    print("\n  结构体大小（用于校验 ctypes 布局）:")
    for name in ("DeviceT", "DevParamsT", "TunerParamsT", "RxChannelParamsT",
                 "ControlParamsT", "CallbackFnsT", "StreamCbParamsT"):
        print(f"    {name:<18} {C.sizeof(globals()[name])} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
