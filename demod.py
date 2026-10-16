# -*- coding: utf-8 -*-
"""
HFDL 解调器（教学实现）+ 仿真调制器。

链路（接收）：
    实音频 → 带通滤波 → NCO 下变频（相位连续）→ 积分-清除（矩形脉冲匹配滤波，
    同时抑制 2f0 镜像）→ 定时粗搜索 → M 次方载波捕获 + 判决导向 PLL
    → 差分 MPSK 判决 → 可选 V.29 解扰。

链路（发射，用于自测/仿真）：
    比特 → 差分 MPSK → 矩形脉冲成形（每符号保持 sps 采样）→ 上变频(+频偏)
    → 高斯噪声 → 实音频。

参考 HFDL 2.x (ARINC 732)：π/4-DQPSK / 8PSK，600~2400 baud，
SSB 音频中心频率典型 1370 Hz。此实现为教学结构，非位级兼容 OpenHFDL。
"""
import numpy as np
from scipy.signal import butter, sosfilt

TWO_PI = 2.0 * np.pi


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def bits_to_val(bits_row):
    v = 0
    for b in bits_row:
        v = (v << 1) | int(b)
    return v


def val_to_bits(v, k):
    return [(v >> (k - 1 - i)) & 1 for i in range(k)]


def frame_to_bits(data: bytes) -> np.ndarray:
    out = []
    for byte in data:
        out.extend((byte >> (7 - i)) & 1 for i in range(8))
    return np.array(out, dtype=np.uint8)


# 支持的调制方式 -> 星座阶数 M（每符号 bit 数 = log2(M)）
#   BPSK : M=2  1 bit/符号    —— HFDL 300/600 bps 使用
#   QPSK : M=4  2 bit/符号    —— HFDL 1200 bps 使用
#   8PSK : M=8  3 bit/符号    —— HFDL 1800 bps 使用
MODULATION_ORDERS = {
    "BPSK": 2,
    "QPSK": 4,
    "4PSK": 4,      # QPSK 别名
    "8PSK": 8,
}

# 可选档位与 GUI 共用同一份定义（hfdl_presets 为纯数据模块，无重依赖）
try:
    from hfdl_presets import (BUILTIN_MODULATIONS as MODULATION_CHOICES,
                              BUILTIN_BAUD_RATES as BAUD_CHOICES)
except Exception:                                   # pragma: no cover
    MODULATION_CHOICES = ["BPSK", "QPSK", "8PSK"]
    BAUD_CHOICES = [300, 600, 1200, 1800, 2400]


def modulation_order(name):
    """调制方式字符串 -> 星座阶数 M（无法识别时回退 8PSK）。"""
    return MODULATION_ORDERS.get(str(name).upper().strip(), 8)


# ----------------------------------------------------------------------
# 仿真发射机
# ----------------------------------------------------------------------
def modulate(bits, sym_rate, modulation, center_freq, sr,
             noise_db=18.0, freq_offset=0.0, seed=None, scramble=False):
    """bits(0/1) -> 实音频 float64。
    差分 MPSK：相位增量 = word * 2π/m（自然二进制），解调端 round(dphi/dstep) 还原。
    scramble=True 时先做 V.29 自同步扰码（对应接收端 descrambler）。"""
    rng = np.random.default_rng(seed)
    bits = np.asarray(bits, dtype=np.uint8).ravel()
    if scramble:
        bits = Scrambler().process(bits)
    m = modulation_order(modulation)
    k = int(np.log2(m))
    pad = (k - len(bits) % k) % k
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, np.uint8)])
    words = np.array([bits_to_val(bits[i:i + k]) for i in range(0, len(bits), k)])
    inc = words * (TWO_PI / m)
    ph = np.cumsum(inc)
    amp = np.exp(1j * ph)

    sps = sr / float(sym_rate)
    # 矩形脉冲成形：符号 i 占据采样区间 [guard + i*sps, guard + (i+1)*sps)。
    # 用逐采样归属方式，精确支持分数 sps，与接收端分数积分-清除一致。
    guard = int(sps)
    n_sym = len(amp)
    n_out = int((n_sym + 16) * sps) + guard + 8
    baseband = np.zeros(n_out, dtype=complex)
    # 逐符号填充（向量化：对每个输出样本计算符号索引）
    idx = np.arange(n_out)
    sym_idx = np.floor((idx - guard) / sps).astype(int)
    valid = (sym_idx >= 0) & (sym_idx < n_sym)
    baseband[valid] = amp[np.clip(sym_idx[valid], 0, n_sym - 1)]

    t = np.arange(len(baseband)) / sr
    x = np.real(baseband * np.exp(1j * TWO_PI * (center_freq + freq_offset) * t))
    x /= (np.max(np.abs(x)) + 1e-12)
    if noise_db is not None and noise_db > 0:
        x = x + rng.normal(0, 10 ** (-noise_db / 20.0), len(x))
    return x


# ----------------------------------------------------------------------
# V.29 自同步扰码/解扰（跨块保持窗口）
#   扰码:   y[i] = x[i] ^ y[i-14] ^ y[i-15]
#   解扰:   x[i] = y[i] ^ y[i-14] ^ y[i-15]
# ----------------------------------------------------------------------
class Scrambler:
    D1, D2 = 14, 15

    def __init__(self):
        self.win = list(np.zeros(self.D2 + 2, dtype=np.uint8))

    def process(self, bits):
        b = np.asarray(bits, dtype=np.uint8) & 1
        out = np.empty(len(b), dtype=np.uint8)
        w = self.win
        for i, v in enumerate(b):
            out[i] = v ^ w[-self.D1] ^ w[-self.D2]
            w.append(int(out[i]))          # 记录扰码输出 y
        if len(w) > 2 * self.D2:
            del w[:len(w) - 2 * self.D2]
        self.win = w
        return out


class Descrambler:
    D1, D2 = 14, 15

    def __init__(self):
        self.win = list(np.zeros(self.D2 + 2, dtype=np.uint8))

    def process(self, bits):
        b = np.asarray(bits, dtype=np.uint8) & 1
        out = np.empty(len(b), dtype=np.uint8)
        w = self.win
        for i, v in enumerate(b):
            out[i] = v ^ w[-self.D1] ^ w[-self.D2]
            w.append(v)                    # 记录输入 y
        if len(w) > 2 * self.D2:
            del w[:len(w) - 2 * self.D2]
        self.win = w
        return out


# ----------------------------------------------------------------------
# 解调器
# ----------------------------------------------------------------------
class Demodulator:
    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log or (lambda s: None)
        self.apply_cfg(cfg)

    # ------------------------------------------------------------------
    def apply_cfg(self, cfg):
        self.cfg = cfg
        sr = cfg.sample_rate
        nyq = sr / 2.0
        f0, bw = cfg.center_freq, cfg.bandwidth
        lo, hi = max(30.0, f0 - bw / 2.0), min(nyq - 30.0, f0 + bw / 2.0)
        # 带通仅作为输入预滤波，用于真实声卡/宽带 SDR 场景；
        # 会引入群延迟与相位畸变。仿真环回（信号已带限）可关闭。
        self._bp_enable = getattr(cfg, "bandpass", True)
        if self._bp_enable and hi > lo + 30:
            self.bp_sos = butter(5, [lo / nyq, hi / nyq], btype="band", output="sos")
            self.bp_zi = np.zeros((self.bp_sos.shape[0], 2))
        else:
            self.bp_sos = None
            self.bp_zi = None

        self.sps = sr / float(cfg.symbol_rate)
        self.m = modulation_order(cfg.modulation)
        self.k = int(np.log2(self.m))
        self.descr = Descrambler() if cfg.descrambler else None

        # 状态
        self.n_total = 0                  # 输入音频全局样本计数
        self.timed = False
        self.locked = False
        self.acq = []
        self.carr = 0.0
        self.carr_int = 0.0
        self.step_rate = 0.0              # 每符号残余角速率 rad
        self.prev_dec = None
        self.pending = np.array([], dtype=complex)   # 未消费的复基带
        self.pending_start = 0             # pending[0] 的全局样本索引
        self.frac_off = 0.0                # 符号边界相对 pending 的分数偏移
        self.stats = {"symbols": 0, "blocks": 0, "bits": 0}

    def reset(self):
        self.apply_cfg(self.cfg)

    # ------------------------------------------------------------------
    def push(self, audio):
        x = np.asarray(audio, dtype=np.float64)
        if len(x) == 0:
            return np.empty(0, dtype=np.uint8)
        self.stats["blocks"] += 1
        pk = float(np.max(np.abs(x)))
        if pk > 1e-6 and pk > 1.0:
            x = x / pk

        # 带通 + 下变频 → 复基带
        z = self._to_baseband(x)

        # 积分-清除 → 符号采样
        syms = self._integrate_dump(z)

        if not self.timed:
            return np.empty(0, dtype=np.uint8)
        if len(syms) == 0:
            return np.empty(0, dtype=np.uint8)

        bits = self._carrier_decide(syms)
        if self.descr is not None and len(bits):
            bits = self.descr.process(bits)
        self.stats["bits"] += len(bits)
        return bits

    # ------------------------------------------------------------------
    def _to_baseband(self, x):
        sr = self.cfg.sample_rate
        n0 = self.n_total
        self.n_total += len(x)
        if self.bp_sos is not None:
            b = sosfilt(self.bp_sos, x, zi=self.bp_zi)
            self.bp_zi = b[1]
            x = b[0]
        t = np.arange(n0, n0 + len(x)) / sr
        return x * np.exp(-1j * TWO_PI * self.cfg.center_freq * t)

    # ------------------------------------------------------------------
    def _integrate_dump(self, z):
        """分数积分-清除：每 sps（可为非整数）个基带采样累加为一个符号。

        符号 k 的积分窗口为 [pos, pos+sps)。用线性插值处理分数边界。
        首次运行时做粗定时（搜索使符号能量最大的采样偏移）。"""
        sps = self.sps
        n = len(z)

        # 累积基带
        if len(self.pending) == 0:
            self.pending_start = self.n_total - n
        self.pending = np.concatenate([self.pending, z])

        if not self.timed:
            need = int(np.ceil(sps * 40))
            if len(self.pending) < need:
                return np.array([], dtype=complex)
            # 粗定时：搜索偏移 ∈ [0, sps)，使符号平均能量最大。
            # 步长随 sps 自适应：低波特率（如 300 baud @48k → sps=160）时
            # 仍保持候选数量在 ~256 以内，避免捕获过慢。
            if sps <= 32:
                step = 0.25
            elif sps <= 64:
                step = 0.5
            elif sps <= 160:
                step = 1.0
            else:
                step = float(max(1.0, sps / 256.0))
            best_off, best_e = 0.0, -1.0
            nwin = 30
            offs = np.arange(0.0, sps, step)
            for off in offs:
                syms = self._frac_integrate(self.pending, off, nwin)
                if len(syms) < nwin:
                    continue
                e = float(np.mean(np.abs(syms) ** 2))
                if e > best_e:
                    best_e, best_off = e, float(off)
            self.timed = True
            # 丢弃采样起点之前的样本
            skip = int(np.floor(best_off))
            self.pending = self.pending[skip:]
            self.pending_start += skip
            self.frac_off = best_off - skip   # 符号边界相对 pending 的分数偏移

        # 流式分数积分-清除
        out = self._frac_integrate_stream()
        return np.array(out, dtype=complex)

    # ------------------------------------------------------------------
    def _frac_integrate(self, buf, off, nsyms):
        """对 buf（基带）从偏移 off 起连续积分 nsyms 个符号。"""
        sps = self.sps
        n = len(buf)
        out = []
        p = off
        for _ in range(nsyms):
            end = p + sps
            if end >= n - 1:
                break
            i0 = int(p)
            f0 = p - i0
            i1 = int(end)
            f1 = end - i1
            if i1 > i0:
                s = np.sum(buf[i0 + 1:i1 + 1])
                s = s - buf[i0] * f0 + buf[i1] * f1
            else:
                s = buf[i0] * (end - p)
            out.append(s)
            p = end
        return np.array(out, dtype=complex)

    # ------------------------------------------------------------------
    def _frac_integrate_stream(self):
        """流式：消费 pending 中可完整积分出的符号，保留未完成窗口。"""
        sps = self.sps
        n = len(self.pending)
        out = []
        p = self.frac_off
        while p + sps < n - 1:
            end = p + sps
            i0 = int(p)
            f0 = p - i0
            i1 = int(end)
            f1 = end - i1
            s = np.sum(self.pending[i0 + 1:i1 + 1]) - self.pending[i0] * f0 + self.pending[i1] * f1
            out.append(s)
            p = end
            if len(out) > 8192:
                break
        # 消费到 p 之前的样本
        consume = int(np.floor(p))
        if consume > 0:
            self.pending = self.pending[consume:]
            self.pending_start += consume
            self.frac_off = p - consume
        else:
            self.frac_off = p
        return out

    # ------------------------------------------------------------------
    def _carrier_decide(self, syms):
        """M 次方载波捕获 + 判决导向 PLL，差分判决。"""
        m, k = self.m, self.k
        dstep = TWO_PI / m
        sym_rate = self.cfg.symbol_rate

        if not self.locked:
            self.acq.extend(syms)
            if len(self.acq) < 400:
                return np.empty(0, dtype=np.uint8)
            s = np.array(self.acq[-4096:], dtype=np.complex128)
            p = s ** m
            nf = 8192
            spec = np.abs(np.fft.fft(p, nf))
            faxis = np.fft.fftfreq(nf)                 # cycles/symbol
            mask = np.abs(faxis) <= 0.25 * m
            j = int(np.argmax(spec[mask]))
            cyc = float(faxis[mask][j])
            dphi = TWO_PI * cyc / m                    # 每符号残余相移 rad
            corr = p * np.exp(-1j * m * dphi * np.arange(len(p)))
            mean_angle = np.angle(np.mean(corr))
            self.step_rate = dphi
            self.carr = float(mean_angle / m)
            self.carr_int = 0.0
            self.acq = []
            self.prev_dec = None
            self.locked = True
            self.log(f"[demod] 载波锁定：频偏≈{dphi*sym_rate/TWO_PI:+.0f} Hz")

        kp, ki = 0.03, 5e-4
        bits = []
        carr, cint, srate = self.carr, self.carr_int, self.step_rate
        prev = self.prev_dec
        for sym in syms:
            carr += srate + cint
            corr = sym * np.exp(-1j * carr)
            if prev is not None:
                dphi = np.angle(corr * np.conj(prev))
                val = int(round(dphi / dstep)) % m
                ideal = val * dstep
                err = np.angle(corr * np.conj(prev) * np.exp(-1j * ideal))
                cint = max(-0.5, min(0.5, cint + ki * err))
                carr += kp * err
                bits.extend(val_to_bits(val, k))
            prev = corr
        self.carr = float(np.angle(np.exp(1j * carr)))
        self.carr_int = cint
        self.prev_dec = prev
        self.stats["symbols"] += len(syms)
        return np.array(bits, dtype=np.uint8)
