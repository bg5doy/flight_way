# -*- coding: utf-8 -*-
"""
HFDL 航班标识解析与规范化。

背景
----
HFDL 性能报文里的 `flight_id`（7 字符）**不是**统一格式 —— 它是机组在航空电子
设备里自行配置的标识，实测同批数据里同时存在三类：

  1. **ICAO 呼号**：3 字母航司代码 + 航班号
        AAL100 (American 100)、ANA107 (ANA 107)、ACA025 (Air Canada 25)
  2. **IATA 商业航班号**：2 字符航司代码 + 航班号（常零填充到固定宽度）
        CA0433 (Air China 433)、3U3192 (Sichuan 3192)、BX0111 (Air Busan 111)
  3. **注册号 / 特殊编码**：B-1234、N12345、D00031、WIN953 等

因此「HFDL 的 flight_id」与「商业航班号」既可能相同、也可能差一步转换：
  · AAL100 ≠ AA100        —— 需 ICAO→IATA 航司代码转换
  · CA0433 ≠ CA433         —— 需去掉零填充
  · 3U3192 = 3U3192        —— 本来就是商业航班号

设计原则（重要）
----------------
**只收录可核实的航司映射。** 查不到 IATA 等价码时**不猜测**，直接回退显示
原始 flight_id —— 原始呼号本身仍是准确信息，而编造的航班号是错的。
本模块不认识的代码会原样显示，不会出现"看起来像但不对"的商业航班号。

HFDL 的 6 字符硬上限（决定了号码位数）
--------------------------------------
HFDL Performance Data PDU 固定 47 字节，其中 flight_id 只占 **6 个字节**
（OpenHFDL `src/hfnpdu.c`：`PERFORMANCE_DATA_HFNPDU_LEN 47`，
`memcpy(result->flight_id, buf + 2, 6)`）。因此：

  · **ICAO 呼号**：3 字母航司码 + N 位数字  →  N ≤ 3
        CCA981（6 字符）✓        CCA1704（7 字符）✗ 装不下
  · **IATA 航班号**：2 字符航司码 + N 位数字  →  N ≤ 4
        CA1704（6 字符）✓        MU5104（6 字符）✓

结论：**国内 4 位航班号只能在 IATA 形式下完整出现**；若机组配置成 ICAO
三字码形式，则第 4 位数字在报文里根本不存在，**无法还原**（补位等于编造）。
实测数据印证：389 个 ICAO 形式呼号全部 ≤3 位数字，70 个 4 位号码全部是
IATA 形式，且 471 个 flight_id 无一超过 6 字符。

`parse_flight_id()` 用 `capped` 标记"号码已顶到 6 字符上限"的 ICAO 形式，
便于界面提示"末位可能未包含在报文里"。

维护提示：新增映射前请确认 ICAO 三字码与 IATA 二字码确实属于同一家航司；
ICAO 三字码不存在 "XXX2" 这种带数字后缀的写法（那不是合法 designator）。
"""

import re

# ======================================================================
# ICAO 三字码 -> IATA 二字码
# 仅收录可核实的对应关系（覆盖 HFDL 上常见的洲际/干线/货运航司）
# ======================================================================
ICAO_TO_IATA = {
    # ---- 亚洲 ----
    "ANA": "NH", "AJX": "NQ", "APJ": "MM", "ADO": "HD", "SFJ": "7G",
    "AAR": "OZ", "ABL": "BX", "ASV": "RS", "KAL": "KE", "JJA": "7C",
    "JNA": "LJ", "TWB": "TW",
    "CCA": "CA", "CSZ": "ZH", "CSN": "CZ", "CES": "MU", "CHH": "HU",
    "CQH": "9C", "CSC": "3U", "CXA": "MF", "CBJ": "JD", "CDG": "SC",
    "CQN": "PN", "HXA": "GX", "DKH": "HO", "RLH": "DR", "UEA": "EU",
    "CSH": "FM",           # 上海航空 Shanghai Airlines（CSH/FM）
    "CNW": "KN",           # 中国联合航空 China United（CNW/KN）
    "YZR": "Y8",           # 扬子江快运 / 金鹏航空 Suparna Airlines（YZR/Y8）
    "CPA": "CX", "HDA": "UO", "CRK": "HX", "AMU": "NX",
    "CAL": "CI", "EVA": "BR", "SJX": "JX", "MDA": "AE",
    "SIA": "SQ", "SLK": "TR", "TGW": "TR",
    "MAS": "MH", "AXM": "AK", "XAX": "D7", "MXD": "OD",
    "THA": "TG", "TAX": "FD", "NOK": "DD", "BKP": "PG",
    "VTI": "UK", "AIC": "AI", "IGO": "6E", "SEJ": "SG", "GOW": "G8",
    "ALK": "UL", "PIA": "PK", "BBC": "BG",
    "HVN": "VN", "VJC": "VJ", "PIC": "BL",
    "GIA": "GA", "LNI": "JT", "BTK": "ID", "CTV": "QG",
    "PAL": "PR", "CEB": "5J", "APG": "Z2",
    "KZR": "KC", "UZB": "HY", "MGL": "OM",
    "NCA": "KZ",           # 日本货物航空 Nippon Cargo Airlines（NCA/KZ）
    "APZ": "YP",           # 韩国 Air Premia（APZ/YP）
    # ---- 中东 ----
    "UAE": "EK", "ETD": "EY", "QTR": "QR", "SVA": "SV", "KAC": "KU",
    "OMA": "WY", "GFA": "GF", "RJA": "RJ", "MEA": "ME", "ELY": "LY",
    "FDB": "FZ", "ABY": "G9", "JZR": "J9", "KNE": "XY",
    # ---- 欧洲 ----
    "BAW": "BA", "EZY": "U2", "EJU": "U2", "VIR": "VS", "EXS": "LS",
    "DLH": "LH", "CLH": "CL", "GEC": "LH", "EWG": "EW", "CFG": "DE",
    "AUA": "OS", "SWR": "LX", "EDW": "WK", "TOM": "BY", "TUI": "BY",
    "KLM": "KL", "TRA": "HV", "AFR": "AF", "TVF": "TO",
    "IBE": "IB", "ANE": "YW", "VLG": "VY", "AEA": "UX", "IBS": "I2",
    "TAP": "TP", "ITY": "AZ", "AZA": "AZ", "SAS": "SK", "NAX": "DY",
    "RYR": "FR", "RYS": "FR", "WZZ": "W6", "LOT": "LO", "CSA": "OK",
    "AEE": "A3", "THY": "TK", "PGT": "PC", "AHY": "J2", "AFL": "SU",
    "FIN": "AY", "EIN": "EI", "NBT": "N0", "LOG": "LM", "BEE": "BE",
    "SWU": "LX", "AEW": "VV",
    # ---- 美洲 ----
    "AAL": "AA", "DAL": "DL", "UAL": "UA", "SWA": "WN", "JBU": "B6",
    "ASA": "AS", "NKS": "NK", "FFT": "F9", "HAL": "HA", "SCX": "SY",
    "ACA": "AC", "WJA": "WS", "JZA": "QK", "GGN": "ZX", "POE": "PD",
    "AMX": "AM", "SLI": "5D", "VOI": "Y4", "VIV": "VB", "AIJ": "4O",
    "LAN": "LA", "TAM": "JJ", "GLO": "G3", "AZU": "AD", "PTB": "2Z",
    "ARG": "AR", "AVA": "AV", "CMP": "CM", "SKU": "H2",
    # ---- 大洋洲 ----
    "QFA": "QF", "JST": "JQ", "VOZ": "VA", "ANZ": "NZ", "RXA": "ZL",
    "QJE": "QF",
    # ---- 非洲 ----
    "SAA": "SA", "ETH": "ET", "KQA": "KQ", "RAM": "AT", "MSR": "MS",
    "TCV": "VR", "ANG": "PX",
    # ---- 货运 ----
    "CKK": "CK", "CAO": "CA", "GTI": "5Y", "FDX": "FX", "UPS": "5X",
    "CLX": "CV", "BOX": "3S", "SQC": "SQ", "GSS": "S7",
    # ---- 其它（按 ICAO 三字码登记表，非干支线）----
    "WIN": "WM",           # Winair 向风群岛航空（WIN/WM）
}

# 已知 IATA 二字码集合（用于判定"这份 flight_id 是否已经是商业航班号"）
KNOWN_IATA = set(ICAO_TO_IATA.values()) | {
    "CA", "MU", "CZ", "HU", "ZH", "MF", "3U", "9C", "SC", "GS", "JD", "PN",
    "EU", "DR", "GX", "HO", "NS", "KN", "RY", "8L", "BK", "AQ", "GJ", "QW",
    "SF", "TV", "UX", "IB", "VY", "D8", "I2", "NT", "PM", "LG", "EN", "F7",
    "5O", "3V", "MB", "9U", "H7", "G4", "SY", "XP", "2P", "DG", "PR", "5J",
    "Z2", "6J", "NH", "JL", "MM", "GK", "IJ", "7G", "BC", "HD", "IT", "NU",
    "6E", "SG", "IX", "G8", "QP", "I5", "UK", "AI", "EK", "EY", "QR", "SV",
    "FZ", "G9", "J9", "XY", "WY", "GF", "RJ", "ME", "LY", "MS", "AT", "KQ",
    "ET", "SA", "TM", "WB", "TC", "HM", "UU", "MK", "DL", "AA", "UA", "AS",
    "B6", "NK", "F9", "HA", "WN", "AC", "WS", "PD", "AM", "Y4", "VB", "LA",
    "JJ", "G3", "AD", "AR", "AV", "CM", "QF", "JQ", "VA", "NZ", "ZL",
    "FM",   # 上海航空（HFDL 里也会直接以 FM 开头）
    "KY",   # 昆明航空 Kunming Airlines
}

# 航司二字码：除已知表外，还接受「数字+字母」形式（3U/9C/5J/6E/7C/8L/2Z…）
_IATA_DIGIT_LETTER = re.compile(r"^[0-9][A-Z]$")

# 航空器注册号特征（放在最前判定，避免与航班号混淆）
#   ⚠ 不要写 ^[A-Z]{2}[0-9]{3,4}$ 这类宽泛规则 —— 会把 CA0433 这种
#     正常航班号误判为注册号。只保留各国注册号真正有辨识度的写法。
_REG_PATTERNS = [
    re.compile(r"^[A-Z]{1,2}-[A-Z0-9]{2,5}$"),   # B-1234 / G-ABCD / D-AIZX / VH-OQA
    re.compile(r"^N[0-9]{1,5}[A-Z]{0,2}$"),      # 美国   N12345 / N123AB
    re.compile(r"^JA[0-9]{1,4}[A-Z]?$"),         # 日本   JA801A
    re.compile(r"^HL[0-9]{1,4}$"),               # 韩国   HL8250
]

_IATA_TO_ICAO = {v: k for k, v in ICAO_TO_IATA.items()}

# HFDL flight_id 字段的硬上限（Performance Data PDU：buf+2 起 6 字节）
HFDL_FLIGHT_ID_MAX_LEN = 6
# 两种形式的号码位数上限（由上面的 6 字符推导）
MAX_DIGITS_ICAO = HFDL_FLIGHT_ID_MAX_LEN - 3      # 3
MAX_DIGITS_IATA = HFDL_FLIGHT_ID_MAX_LEN - 2      # 4


def _is_alpha(s):
    return bool(s) and all("A" <= c <= "Z" for c in s)


def _is_digit(s):
    return bool(s) and all("0" <= c <= "9" for c in s)


def _known_iata(code):
    """是否为可识别的 IATA 二字码。"""
    return code in KNOWN_IATA or bool(_IATA_DIGIT_LETTER.match(code))


def _iata_to_icao(code):
    return _IATA_TO_ICAO.get(code, "")


def _looks_like_reg(s):
    return any(p.match(s) for p in _REG_PATTERNS)


def parse_flight_id(fid):
    """解析 HFDL flight_id。

    返回 dict:
      kind    : 'icao' | 'iata' | 'reg' | 'unknown'
      icao    : ICAO 呼号（如 AAL100 / CCA433）
      iata    : IATA 商业航班号（如 AA100 / CA433）；无法转换时为空串
      number  : 航班数字部分（已去零填充）
      airline : 航司代码原文
      capped  : ICAO 形式且已顶到 6 字符上限（3 字母 + 3 位数字）——
                该航班号可能还有第 4 位数字，但 HFDL 报文装不下，无法还原
    """
    out = {"raw": fid or "", "kind": "unknown", "icao": "", "iata": "",
           "number": "", "airline": "", "capped": False}
    s = (fid or "").strip().upper().replace(" ", "")
    if not s:
        return out

    # ---- 0) 注册号优先（特征最明显，避免被航班号规则误吞）----
    if _looks_like_reg(s):
        out.update(kind="reg", number="")
        return out

    # ---- 1) 3 字母 ICAO 代码 + 数字 ----
    if len(s) >= 4:
        p3, rest = s[:3], s[3:]
        if _is_alpha(p3) and _is_digit(rest):
            iata_code = ICAO_TO_IATA.get(p3)
            num = rest.lstrip("0") or "0"
            # 6 字符 = 3 字母 + 3 位数字 → 号码顶到字段上限，可能少了末位
            out.update(kind="icao", airline=p3, number=num,
                       icao=f"{p3}{rest}",
                       capped=(len(s) == HFDL_FLIGHT_ID_MAX_LEN),
                       iata=(f"{iata_code}{num}" if iata_code else ""))
            return out

    # ---- 2) 2 字符 IATA 代码 + 数字 ----
    if len(s) >= 3:
        p2, rest = s[:2], s[2:]
        if _is_digit(rest) and _known_iata(p2):
            num = rest.lstrip("0") or "0"
            icao_code = _iata_to_icao(p2)
            out.update(kind="iata", airline=p2, number=num,
                       iata=f"{p2}{num}",
                       icao=(f"{icao_code}{num}" if icao_code else ""))
            return out

    # ---- 3) 其它（D00031 / 测试编码 / 无法识别）----
    # 不做猜测，原样保留；显示层会回退到原文
    out.update(kind="unknown", number=s)
    return out


def display_flight_id(fid, mode="raw"):
    """按模式给出用于显示的航班号。

    mode:
      raw  —— 原样显示 HFDL 的 flight_id（如 CA0433 / AAL100）
      iata —— 规范化为商业航班号（如 CA433 / AA100）；无法转换时回退原文
      both —— 两者都显示（如 CA433 (CA0433)）；相同则只显示一次
    """
    info = parse_flight_id(fid)
    raw = info["raw"].strip().upper() or "?"
    if mode == "raw":
        return raw
    norm = info["iata"] or info["icao"] or raw
    if mode == "iata":
        return norm
    if mode == "both":
        return norm if norm == raw else f"{norm} ({raw})"
    return raw


def conversion_stats(ids):
    """统计一批 flight_id 的转换情况，便于自检。"""
    from collections import Counter
    kinds = Counter()
    unmapped = []
    for fid in ids:
        info = parse_flight_id(fid)
        kinds[info["kind"]] += 1
        if info["kind"] == "icao" and not info["iata"]:
            unmapped.append(fid)
    return {"total": len(ids), "kinds": dict(kinds),
            "unmapped_icao": sorted(set(unmapped))}
