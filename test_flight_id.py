# -*- coding: utf-8 -*-
"""HFDL 航班标识解析测试：ICAO 呼号 / IATA 商业航班号 / 注册号 的识别与转换。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flight_id import (HFDL_FLIGHT_ID_MAX_LEN, MAX_DIGITS_IATA,
                       MAX_DIGITS_ICAO, display_flight_id, parse_flight_id)

# (原始, 期望 kind, 期望 iata, 期望 number)
CASES = [
    # ---- IATA 商业航班号（含零填充，需去填充）----
    ("CA0433", "iata", "CA433", "433"),
    ("CA1721", "iata", "CA1721", "1721"),
    ("CA0927", "iata", "CA927", "927"),
    ("3U3192", "iata", "3U3192", "3192"),
    ("BX0111", "iata", "BX111", "111"),
    ("9C8801", "iata", "9C8801", "8801"),
    ("MU510", "iata", "MU510", "510"),
    ("CZ350", "iata", "CZ350", "350"),
    ("QF12", "iata", "QF12", "12"),
    # ---- ICAO 三字呼号（需转 IATA）----
    ("AAL100", "icao", "AA100", "100"),
    ("ANA107", "icao", "NH107", "107"),
    ("ACA025", "icao", "AC25", "25"),
    ("AIC357", "icao", "AI357", "357"),
    ("AMX091", "icao", "AM91", "91"),
    ("APJ571", "icao", "MM571", "571"),
    ("AAR101", "icao", "OZ101", "101"),
    ("SIA322", "icao", "SQ322", "322"),
    ("BAW256", "icao", "BA256", "256"),
    ("CCA981", "icao", "CA981", "981"),
    ("DLH400", "icao", "LH400", "400"),
    ("UAE1", "icao", "EK1", "1"),
    # ---- 新增可核实映射（本批实测数据里出现的）----
    ("CSH816", "icao", "FM816", "816"),      # 上海航空 CSH/FM
    ("CNW123", "icao", "KN123", "123"),      # 中国联合航空 CNW/KN
    ("NCA108", "icao", "KZ108", "108"),      # 日本货物航空 NCA/KZ
    ("APZ732", "icao", "YP732", "732"),      # Air Premia APZ/YP
    ("WIN953", "icao", "WM953", "953"),      # Winair WIN/WM
    ("YZR9008", "icao", "Y89008", "9008"),   # 金鹏航空 YZR/Y8
    # ---- 无 IATA 映射 → 必须回退原文（不猜）----
    ("LKE8221", "icao", "", "8221"),
    ("HKC949", "icao", "", "949"),
    # ---- 注册号 ----
    ("B-1234", "reg", "", ""),
    ("N12345", "reg", "", ""),
    ("JA801A", "reg", "", ""),
    # ---- 未知 ----
    ("????", "unknown", "", "????"),
]

MODES = {
    "CA0433": ("CA0433", "CA433", "CA433 (CA0433)"),
    "AAL100": ("AAL100", "AA100", "AA100 (AAL100)"),
    "3U3192": ("3U3192", "3U3192", "3U3192"),
}

ok = True
print(f"{'原始':<9}{'类型':<9}{'ICAO':<9}{'IATA':<9}{'号码':<7} 判定")
print("-" * 74)
for raw, kind, iata, num in CASES:
    i = parse_flight_id(raw)
    good = (i["kind"] == kind and i["iata"] == iata and i["number"] == num)
    ok &= good
    print(f"{raw:<9}{i['kind']:<9}{i['icao'] or '-':<9}{i['iata'] or '-':<9}"
          f"{i['number'] or '-':<7} {'OK' if good else 'FAIL'}"
          f"{'' if good else f'  期望 kind={kind} iata={iata} num={num}'}")

print("\n=== 三种显示模式 ===")
for raw, (m_raw, m_iata, m_both) in MODES.items():
    got = (display_flight_id(raw, "raw"), display_flight_id(raw, "iata"),
           display_flight_id(raw, "both"))
    good = got == (m_raw, m_iata, m_both)
    ok &= good
    print(f"  {raw:<8} raw={got[0]:<12} iata={got[1]:<12} both={got[2]:<20} "
          f"{'OK' if good else 'FAIL'}")

print("\n=== 边界情况 ===")
edge = [("", "raw", "?"), ("", "iata", "?"), ("   ca0433 ", "iata", "CA433"),
        ("ca0433", "iata", "CA433"), ("CA0001", "iata", "CA1")]
for raw, mode, want in edge:
    got = display_flight_id(raw, mode)
    good = got == want
    ok &= good
    print(f"  {raw!r:<14} mode={mode:<5} -> {got!r:<10} "
          f"{'OK' if good else f'FAIL (期望 {want!r})'}")

# ----------------------------------------------------------------------
# HFDL 6 字符字段上限：ICAO 三字码最多 3 位数字，IATA 二字码最多 4 位
# ----------------------------------------------------------------------
print("\n=== HFDL 6 字符上限 ===")
print(f"  字段上限 {HFDL_FLIGHT_ID_MAX_LEN} 字符 -> "
      f"ICAO 最多 {MAX_DIGITS_ICAO} 位数字，IATA 最多 {MAX_DIGITS_IATA} 位")
limit_cases = [
    # (flight_id, 期望 iata, 期望 capped, 说明)
    ("CCA170", "CA170", True, "6字符 ICAO 形式，号码顶到上限"),
    ("CCA1704", "CA1704", False, "7字符真实报文不存在；解析器不崩、不误标截断"),
    ("CA1704", "CA1704", False, "IATA 形式，4 位号码完整保留"),
    ("MU5104", "MU5104", False, "IATA 形式，4 位号码完整保留"),
    ("CZ3502", "CZ3502", False, "IATA 形式，4 位号码完整保留"),
    ("MF3558", "MF3558", False, "IATA 形式，4 位号码完整保留"),
]
for raw, want_iata, want_capped, note in limit_cases:
    i = parse_flight_id(raw)
    good = (i["iata"] == want_iata and i["capped"] == want_capped)
    ok &= good
    print(f"  {raw:<9} iata={i['iata'] or '-':<9} capped={str(i['capped']):<5} "
          f"{'OK' if good else 'FAIL'}  {note}")

print("\n结果:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
