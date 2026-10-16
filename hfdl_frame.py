# -*- coding: utf-8 -*-
"""
HFDL 帧组包与解析（教学实现，遵循 ARINC 732 公开结构思想）：
  比特流 → 帧头同步(0xF3 0xF3) → 长度/模式 → 载荷 → CRC-16/CCITT-FALSE 校验
  → 字段解析（航班号、注册号、位置、航向、高度、报文文本）。

真实 HFDL 帧格式为 72/288 bit 块、含扰码与 R-S 编码；本实现保留其
“同步字 + 头 + 载荷 + CRC” 框架，可对接 OpenHFDL 解码库替换。
"""
import re
import struct
import time
from dataclasses import dataclass, field as f_field

SYNC = 0xF3
CRC_POLY = 0x1021


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class HfdlFrame:
    raw: bytes = b""
    ts: float = f_field(default_factory=time.time)
    crc_ok: bool = False
    mode: int = -1
    label: str = ""
    reg: str = ""
    flight: str = ""
    lat: float = None
    lon: float = None
    heading: float = None
    altitude: int = None
    speed: int = None
    text: str = ""
    err: str = ""

    def position_ok(self):
        return (self.lat is not None and self.lon is not None
                and -90 <= self.lat <= 90 and -180 <= self.lon <= 180)

    def key(self):
        return (self.flight or self.reg or f"UNK-{self.raw[:4].hex()}").upper()


# ---------------------------------------------------------------------
# 帧同步
# ---------------------------------------------------------------------
class FrameAssembler:
    """从解调比特流中找帧：F3 F3 [mode|len] ... CRC16。"""

    SHORT_BITS, LONG_BITS = 72, 288

    def __init__(self, cfg, log=None, on_frame=None):
        self.cfg = cfg
        self.log = log or (lambda s: None)
        self.on_frame = on_frame
        self.acc = []                 # 0/1 list
        self.frames_found = 0
        self.bytes_seen = 0

    def push_bits(self, bits):
        """输入 0/1 数组；返回解析出的帧列表，并通过 on_frame 回调。

        解调器输出的是连续比特流，帧同步可出现在任意比特偏移，
        因此逐比特扫描同步字（而非仅字节对齐）。"""
        out = []
        self.acc.extend(int(b) & 1 for b in bits)
        buf = self.acc
        n = len(buf)
        SB = self.cfg.sync_byte
        i = 0
        while i + 24 <= n:
            if _byte(buf, i) == SB and _byte(buf, i + 8) == SB:
                b2 = _byte(buf, i + 16)
                blen = self.LONG_BITS if (b2 & 0x01) else self.SHORT_BITS
                total = blen          # 帧总比特数（已含 sync+mode+载荷+CRC）
                if i + total > n:
                    break
                block = bytes(_byte(buf, i + k * 8) for k in range(total // 8))
                body, crc = block[:-2], int.from_bytes(block[-2:], "big")
                if crc16_ccitt_false(body) == crc:
                    fr = HfdlFrame(raw=block, crc_ok=True)
                    parse_payload(fr, self.cfg)
                    out.append(fr)
                    self.frames_found += 1
                    if self.on_frame:
                        self.on_frame(fr)
                    i += total
                else:
                    i += 1
            else:
                i += 1
        if i:
            del buf[:i]
        return out


def _byte(bits, start):
    v = 0
    for k in range(8):
        v = (v << 1) | bits[start + k]
    return v


# ---------------------------------------------------------------------
# 载荷解析（多策略，尽力而为）
# ---------------------------------------------------------------------
_RE_FLIGHT = re.compile(r"\b([A-Z]{2}[0-9A-Z]{2,4})\b")
_RE_DMS = re.compile(r"([NS])(\d{2,3})\s*[:.\-]\s*(\d{2}(?:\.\d+)?)")
_RE_DMS2 = re.compile(r"([EW])(\d{2,3})\s*[:.\-]\s*(\d{2}(?:\.\d+)?)")
_RE_FL = re.compile(r"FL\s*(\d{2,3})")
_RE_FT = re.compile(r"(\d{3,5})\s*(FT|FEET)")
_RE_SPD = re.compile(r"(\d{2,4})\s*(KT|KTS|KNOTS)")
_RE_HDG = re.compile(r"(?:HDG|heading)[^0-9]{0,4}(\d{1,3})")


def parse_payload(fr: HfdlFrame, cfg=None):
    raw = fr.raw
    # ---- 头字段（教学格式）----
    if len(raw) >= 4:
        fr.mode = raw[2] & 0x3F
        fr.label = chr(raw[3]) if 32 <= raw[3] < 127 else ""
    body = raw[4:-2] if len(raw) > 6 else b""

    # ---- 二进制长帧解析（优先）：[reg6][flt6][lat f32][lon f32][hdg u16][alt u16][spd u16][text4] ----
    if len(raw) >= 36 and (raw[2] & 0x01):
        try:
            reg = raw[4:10].decode("ascii", "ignore").strip(" =@.-")
            if reg.replace("-", "").isalnum():
                fr.reg = reg.upper().replace("-", "")
            flt = raw[10:16].decode("ascii", "ignore").strip(" =@.-")
            if flt:
                fr.flight = flt.upper().replace("-", "")
            lat, lon = struct.unpack(">ff", raw[16:24])
            hdg, alt, spd = struct.unpack(">HHH", raw[24:30])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                fr.lat, fr.lon = lat, lon
            if 0 <= hdg < 360:
                fr.heading = float(hdg)
            if 0 < alt < 100000:
                fr.altitude = int(alt)
            if 0 < spd < 2000:
                fr.speed = int(spd)
            fr.text = raw[30:34].decode("ascii", "ignore").strip()
        except Exception:
            pass

    # ---- 文本/宽松解析（兜底）----
    txt = _ascii(body)
    if not fr.text:
        fr.text = txt

    if not fr.reg:
        r6 = _ascii(raw[4:10]).strip(" =@.-")
        if 3 <= len(r6) <= 8 and r6.replace("-", "").isalnum():
            fr.reg = r6.upper().replace("-", "")
    if not fr.flight:
        f6 = _ascii(raw[10:16]).strip(" =@.-")
        if 2 <= len(f6) <= 8:
            cand = f6.replace(" ", "").replace("-", "")
            m = _RE_FLIGHT.search(cand)
            if m:
                fr.flight = m.group(1)
            elif cand.isalnum():
                fr.flight = cand.upper()
        if not fr.flight:
            m = _RE_FLIGHT.search(txt)
            if m:
                fr.flight = m.group(1)

    if fr.lat is None:
        pos_dec = re.search(r"POS\s+(-?\d{1,3}\.\d+)\s+(-?\d{1,3}\.\d+)", txt)
        if pos_dec:
            fr.lat = float(pos_dec.group(1))
            fr.lon = float(pos_dec.group(2))
    if fr.lat is None:
        p1 = _RE_DMS.search(txt)
        p2 = _RE_DMS2.search(txt)
        if p1 and p2:
            fr.lat = _dms(p1.group(1), p1.group(2), p1.group(3))
            fr.lon = _dms(p2.group(1), p2.group(2), p2.group(3))
    if fr.lat is None:
        dec = _decode_acars_pos(body)
        if dec:
            fr.lat, fr.lon, fr.heading = dec
    if fr.lat is None:
        mm = re.search(r"N(\d{1,2}\.\d{1,4})\s*[,\s]\s*W(\d{1,3}\.\d{1,4})", txt)
        if mm:
            fr.lat, fr.lon = float(mm.group(1)), -float(mm.group(2))

    if fr.altitude is None:
        am = _RE_FL.search(txt) or _RE_FT.search(txt)
        if am:
            fr.altitude = int(am.group(1)) * (100 if _RE_FL.search(txt) else 1)
    if fr.speed is None:
        sm = _RE_SPD.search(txt)
        if sm:
            fr.speed = int(sm.group(1))
    if fr.heading is None:
        hm = _RE_HDG.search(txt)
        if hm:
            fr.heading = float(hm.group(1)) % 360
    return fr


def _ascii(b):
    return "".join(chr(x) for x in b if 32 <= x < 127)


def _dms(hemi, deg, mins):
    try:
        v = float(deg) + float(mins) / 60.0
        return -v if hemi in ("S", "W") else v
    except Exception:
        return None


def _decode_acars_pos(body):
    """ACARS 位置报编码兜底解析（多种已知变体）。"""
    if len(body) < 8:
        return None
    for off in range(0, len(body) - 7):
        w = body[off:off + 8]
        try:
            b = int.from_bytes(w, "big")
            # ARINC 位置编码: 20bit lat(90/2^20) + 21bit lon(180/2^20)
            lat = (b >> 44) & 0xFFFFF
            lon = (b >> 23) & 0x1FFFFF
            hdg = b & 0x1FF
            la = lat * (90.0 / (1 << 20))
            lo = lon * (180.0 / (1 << 20)) - 180.0
            if 0.05 < abs(la) < 89.5 and abs(lo) < 179.9:
                return la, lo, hdg * 1.40625
        except Exception:
            pass
    return None
