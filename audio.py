# -*- coding: utf-8 -*-
"""
声卡采集模块：枚举/选择输入设备，回调式实音频数据流。

Windows 上的重要事实（本模块据此去重）：
  PortAudio 会**按主机 API 重复枚举**同一物理设备。例如接一个设备时：
      MME[1] / DirectSound[10] / WASAPI[23] / WDM-KS[31]
  全都指向同一个 IC-7300。此外 MME 与 DirectSound 还会各报一个
  **假的聚合设备**（Microsoft Sound Mapper - Input、主声音捕获驱动程序）。

因此本模块：
  · 按「归一化设备名」去重，默认优先 **WASAPI**（可避开 Windows 音频增强，
    对 PSK 数据解调更有利），其次 WDM-KS / DirectSound / MME
  · 可选按指定主机 API 枚举（GUI 里有下拉框）
  · 支持 WASAPI 独占模式（绕过 Windows 音频引擎，最接近位精确）
"""
import queue
import re

import numpy as np

try:
    import sounddevice as sd
except Exception:  # 无音频环境（如 headless）
    sd = None


# ----------------------------------------------------------------------
# 主机 API 偏好（越靠前越优先）
# ----------------------------------------------------------------------
HOSTAPI_RANK = {
    "Windows WASAPI": 0,
    "Windows WDM-KS": 1,
    "Windows DirectSound": 2,
    "MME": 3,
    "ASIO": 4,
    # 非 Windows
    "ALSA": 0,
    "JACK Audio Connection Kit": 1,
    "PulseAudio": 2,
    "Core Audio": 0,
    "OSS": 3,
}

# 主机 API 短标签（用于设备显示名）
HOSTAPI_TAG = {
    "Windows WASAPI": "WASAPI",
    "Windows WDM-KS": "WDM-KS",
    "Windows DirectSound": "DS",
    "MME": "MME",
}

# 需要过滤掉的「假」聚合设备
_FAKE_DEVICE_PATTERNS = (
    "microsoft sound mapper",
    "主声音捕获驱动程序",
    "主声音驱动程序",
    "primary sound capture driver",
    "primary sound driver",
    "default audio device",
)

# 设备名外层的用途前缀： 「麦克风 (X)」→ X
_WRAPPER_RE = re.compile(
    r"^\s*(?:麦克风|话筒|麦克|线路输入|线性输入|立体声混音|扬声器|"
    r"microphone|mic|line in|stereo mix|speakers?|aux|what u hear|"
    r"wave out mix|mono mix)\s*[（(]\s*(.+?)\s*[)）]\s*$",
    re.IGNORECASE,
)
# Windows 实例前缀："4- USB Audio CODEC"
_INSTANCE_RE = re.compile(r"^\d+\s*-\s*")


def _is_fake(name):
    n = str(name).strip().lower()
    return any(p in n for p in _FAKE_DEVICE_PATTERNS)


def strip_wrapper(name):
    """去掉 Windows 的用途包装：「麦克风 (USB Audio CODEC)」-> 「USB Audio CODEC」。"""
    s = str(name).strip()
    m = _WRAPPER_RE.match(s)
    return m.group(1).strip() if m else s


def normalize_name(name):
    """归一化设备名，用于跨主机 API 去重。

    注意：**不能把通道数纳入分组键**——同一物理设备在不同主机 API 下会报告
    不同通道数（例如 IC-7300 在 WASAPI 报 1ch、在 MME/WDM-KS 报 2ch），
    否则去重会失效。
    """
    s = strip_wrapper(name)
    s = _INSTANCE_RE.sub("", s)          # 去 "4- " 实例前缀
    s = s.replace("_", " ").strip().lower()
    return re.sub(r"\s+", " ", s)


def display_name(d):
    """设备的简洁显示名（去包装、去实例前缀）。"""
    s = strip_wrapper(d["name"])
    s = _INSTANCE_RE.sub("", s).strip()
    return s or d["name"]


# ----------------------------------------------------------------------
# 枚举
# ----------------------------------------------------------------------
def list_hostapis():
    """返回 [(索引, 名称, 输入设备数), ...]。"""
    if sd is None:
        return []
    out = []
    try:
        for i, a in enumerate(sd.query_hostapis()):
            out.append((i, a.get("name", f"api{i}"), len(a.get("devices", []))))
    except Exception:
        pass
    return out


def hostapi_name(idx):
    for i, n, _ in list_hostapis():
        if i == idx:
            return n
    return f"api{idx}"


def enumerate_input_devices():
    """列出**所有**输入设备（每个主机 API 一条，不去重）。

    返回 [dict(index, name, pure_name, hostapi, hostapi_name, channels,
               default_samplerate, is_fake), ...]
    """
    if sd is None:
        return []
    apis = sd.query_hostapis()
    out = []
    try:
        devs = sd.query_devices()
    except Exception:
        return []
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) <= 0:
            continue
        api = apis[d["hostapi"]].get("name", "?") if d["hostapi"] < len(apis) else "?"
        out.append(dict(
            index=i,
            name=d["name"],
            pure_name=strip_wrapper(d["name"]),
            hostapi=d["hostapi"],
            hostapi_name=api,
            channels=d["max_input_channels"],
            default_samplerate=d.get("default_samplerate", 48000),
            is_fake=_is_fake(d["name"]),
        ))
    return out


def dedupe_input_devices(hostapi_name_filter=None, include_fake=False):
    """去重后的输入设备列表（同一物理设备只保留一个最优主机 API 条目）。

    参数
      hostapi_name_filter  只保留该主机 API 的设备（如 "Windows WASAPI"）
      include_fake         是否保留假的聚合设备（默认过滤）
    分组键只用「归一化设备名」——同一物理设备在不同主机 API 下会报告不同
    通道数（IC-7300: WASAPI 1ch / MME 2ch），把通道数计入键会导致去重失效。
    """
    devs = enumerate_input_devices()
    if not include_fake:
        devs = [d for d in devs if not d["is_fake"]]
    if hostapi_name_filter:
        devs = [d for d in devs if d["hostapi_name"] == hostapi_name_filter]

    groups = {}
    for d in devs:
        groups.setdefault(normalize_name(d["name"]), []).append(d)

    best = []
    for key, lst in groups.items():
        lst.sort(key=lambda d: (HOSTAPI_RANK.get(d["hostapi_name"], 9), d["index"]))
        best.append(lst[0])
    best.sort(key=lambda d: normalize_name(d["name"]))
    return best


def fallback_indices(device_index):
    """给定设备索引，返回**同一物理设备**在其它主机 API 下的备选索引（按推荐度）。

    用途：WASAPI 打开失败时自动回退到 WDM-KS / DirectSound / MME。
    """
    devs = enumerate_input_devices()
    target = next((d for d in devs if d["index"] == device_index), None)
    if target is None:
        return []
    key = normalize_name(target["name"])
    cands = [d for d in devs if normalize_name(d["name"]) == key
             and d["index"] != device_index and not d["is_fake"]]
    cands.sort(key=lambda d: (HOSTAPI_RANK.get(d["hostapi_name"], 9), d["index"]))
    return [d["index"] for d in cands]


def list_input_devices(hostapi_name_filter=None, include_fake=False):
    """向后兼容接口：返回 (index, display_name, default_samplerate) 三元组。

    display_name 会带主机 API 短标签，例如：
        [23] USB Audio CODEC · WASAPI
    """
    out = []
    for d in dedupe_input_devices(hostapi_name_filter, include_fake):
        tag = HOSTAPI_TAG.get(d["hostapi_name"])
        disp = display_name(d)
        if tag:
            disp = f"{disp} · {tag}"
        out.append((d["index"], disp, d["default_samplerate"]))
    return out


# ----------------------------------------------------------------------
# 采集
# ----------------------------------------------------------------------
class AudioSource:
    """从声卡采集实数音频 (float32, 单声道)。"""

    def __init__(self, device_index=-1, sample_rate=48000, blocksize=4096,
                 log=None, exclusive=False):
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.log = log or (lambda s: None)
        self.exclusive = bool(exclusive)
        self.q = queue.Queue(maxsize=256)
        self._stream = None
        self.running = False

    # ---------- 设备 ----------
    @staticmethod
    def list_input_devices(hostapi_name_filter=None, include_fake=False):
        """去重后的输入设备（见模块级 list_input_devices）。"""
        return list_input_devices(hostapi_name_filter, include_fake)

    # ---------- 控制 ----------
    def start(self):
        if sd is None:
            raise RuntimeError("sounddevice 不可用（无音频环境？）")

        def _open(dev_idx, extra=None):
            kwargs = dict(
                samplerate=self.sample_rate,
                device=(dev_idx if dev_idx is not None and dev_idx >= 0 else None),
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                callback=self._callback,
            )
            if extra is not None:
                kwargs["extra_settings"] = extra
            st = sd.InputStream(**kwargs)
            st.start()
            return st

        # 依次尝试：首选设备（独占→共享）→ 同一物理设备的其它主机 API
        tried = []
        cands = [self.device_index] + fallback_indices(self.device_index)
        last_err = None
        for dev_idx in cands:
            for extra in ([self._extra_settings_for(dev_idx)]
                          if self.exclusive else [None, ]):
                try:
                    self._stream = _open(dev_idx, extra)
                    self.device_index = dev_idx
                    self.running = True
                    self._log_opened(dev_idx, exclusive=extra is not None)
                    return
                except Exception as e:
                    last_err = e
                    api = ""
                    try:
                        api = hostapi_name(sd.query_devices(dev_idx)["hostapi"])
                    except Exception:
                        pass
                    tried.append(f"[{dev_idx}]{api}"
                                 f"{'/独占' if extra is not None else ''}: {e}")
        raise RuntimeError("所有候选设备/主机API均打开失败：\n  "
                           + "\n  ".join(tried) if tried else str(last_err))

    def _extra_settings_for(self, dev_idx):
        """若该设备属于 WASAPI 则返回独占设置，否则 None。"""
        if not self.exclusive or sd is None:
            return None
        try:
            api = hostapi_name(sd.query_devices(dev_idx)["hostapi"])
            if "WASAPI" in str(api):
                return sd.WasapiSettings(exclusive=True)
        except Exception as e:
            self.log(f"[audio] 独占模式不可用，回退共享模式: {e}")
        return None

    def _log_opened(self, dev_idx, exclusive=False):
        try:
            d = sd.query_devices(dev_idx)
            dev = (f"[{dev_idx}] {strip_wrapper(d['name'])} "
                   f"({hostapi_name(d['hostapi'])})")
        except Exception:
            dev = str(dev_idx)
        mode = " 独占" if exclusive else ""
        self.log(f"[audio] 已打开输入: {dev} @ {self.sample_rate} Hz{mode}")

    def stop(self):
        self.running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def get_block(self, timeout=0.5):
        """返回一个音频块 np.float32 数组，或 None。"""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    # ---------- 内部 ----------
    def _callback(self, indata, frames, t, status):
        if status:
            self.log(f"[audio] 警告: {status}")
        if not self.running:
            return
        block = indata[:, 0].copy()
        try:
            self.q.put_nowait(block)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(block)
            except queue.Full:
                pass


if __name__ == "__main__":
    # 自检：对比「原始枚举」与「去重后」
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raw = enumerate_input_devices()
    print(f"=== 原始枚举：{len(raw)} 条 ===")
    for d in raw:
        print(f"[{d['index']:>2}] {d['hostapi_name']:>20} {d['channels']}ch  "
              f"{d['name']}{'   <假设备>' if d['is_fake'] else ''}")
    print(f"\n=== 去重后：{len(list_input_devices())} 条（优先 WASAPI）===")
    for i, n, sr in list_input_devices():
        print(f"[{i:>2}] {n}")
    print(f"\n=== 指定 WASAPI ===")
    for i, n, sr in list_input_devices(hostapi_name_filter="Windows WASAPI"):
        print(f"[{i:>2}] {n}")
