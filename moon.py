"""
moon.py — 月相事件（朔、上弦、望、下弦）精確時刻計算
純標準庫、不需 astral。以日月視黃經差（elongation）= 0 / 90 / 180 / 270 度求根，
月球取 Meeus《Astronomical Algorithms》Ch.47 前 13 項主要週期項，
太陽取中心差前 3 項；實測與公告時刻誤差在數分鐘內，對「哪一天」的判定足夠。

對外介面：
    month_moon_events(year, month)  → [(day_x, quarter)]   day_x = 日 + 時/24（台灣時間）
    month_moon_symbols(year, month) → {日: 符號字元}        供表格報表使用
    quarter：0=朔(新月) 1=上弦 2=望(滿月) 3=下弦
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from functools import lru_cache

TZ_HOURS = 8   # 台灣標準時間 UTC+8

# 符號約定（黑=暗面）：與既有報表一致，上弦 ◐、下弦 ◑
QUARTER_SYMBOLS = {0: '●', 1: '◐', 2: '○', 3: '◑'}

_J2000 = datetime(2000, 1, 1, 12, 0, 0)


def _jd(dt_utc: datetime) -> float:
    return 2451545.0 + (dt_utc - _J2000).total_seconds() / 86400.0


def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def _elongation(jd: float) -> float:
    """月球視黃經 − 太陽視黃經（度，0–360）。"""
    t = (jd - 2451545.0) / 36525.0
    lp = 218.3164477 + 481267.88123421 * t      # 月球平黃經
    d  = 297.8501921 + 445267.1114034  * t      # 日月平角距
    m  = 357.5291092 + 35999.0502909   * t      # 太陽平近點角
    mp = 134.9633964 + 477198.8675055  * t      # 月球平近點角
    f  = 93.2720950  + 483202.0175233  * t      # 月球升交點角距

    moon = (lp
            + 6.288774 * _sin(mp)
            + 1.274027 * _sin(2 * d - mp)
            + 0.658314 * _sin(2 * d)
            + 0.213618 * _sin(2 * mp)
            - 0.185116 * _sin(m)
            - 0.114332 * _sin(2 * f)
            + 0.058793 * _sin(2 * d - 2 * mp)
            + 0.057066 * _sin(2 * d - m - mp)
            + 0.053322 * _sin(2 * d + mp)
            + 0.045758 * _sin(2 * d - m)
            - 0.040923 * _sin(m - mp)
            - 0.034720 * _sin(d)
            - 0.030383 * _sin(m + mp))

    l0 = 280.46646 + 36000.76983 * t            # 太陽平黃經
    c = ((1.914602 - 0.004817 * t) * _sin(m)
         + 0.019993 * _sin(2 * m)
         + 0.000289 * _sin(3 * m))
    sun = l0 + c

    return (moon - sun) % 360.0


def _signed_diff(jd: float, target: float) -> float:
    """elongation 與目標角的差，折成 [-180, 180)。"""
    return (_elongation(jd) - target + 180.0) % 360.0 - 180.0


def _refine(jd_lo: float, jd_hi: float, target: float) -> float:
    """在 [lo, hi]（差值由負轉正）以二分法求根，精度約 1 秒。"""
    for _ in range(40):
        mid = (jd_lo + jd_hi) / 2.0
        if _signed_diff(mid, target) < 0:
            jd_lo = mid
        else:
            jd_hi = mid
    return (jd_lo + jd_hi) / 2.0


@lru_cache(maxsize=None)
def _events_utc(start: datetime, end: datetime) -> tuple:
    """回傳 [start, end] 內所有 (UTC datetime, quarter)，依時間排序。"""
    step = 1.0 / 8.0                            # 3 小時取樣；月相角速度約 12°/天
    jd0, jd1 = _jd(start), _jd(end)
    out = []
    for q in range(4):
        target = q * 90.0
        jd = jd0
        prev = _signed_diff(jd, target)
        while jd < jd1:
            nxt = jd + step
            cur = _signed_diff(nxt, target)
            # 由負轉正且兩端都在 ±90° 內 → 真正穿越（排除 ±180° 折返）
            if prev < 0 <= cur and abs(prev) < 90 and abs(cur) < 90:
                root = _refine(jd, nxt, target)
                out.append((_J2000 + timedelta(days=root - 2451545.0), q))
            jd, prev = nxt, cur
    out.sort()
    return tuple(out)


@lru_cache(maxsize=None)
def month_moon_events(year: int, month: int) -> list[tuple[float, int]]:
    """
    該（台灣時間）月份內的月相事件 [(day_x, quarter)]，
    day_x = 當地日期 + 當地時間/24（與時序圖 x 軸 day + hour/24 同座標）。
    """
    first = datetime(year, month, 1)
    nxt   = datetime(year + (month == 12), month % 12 + 1, 1)
    utc_start = first - timedelta(hours=TZ_HOURS + 24)
    utc_end   = nxt   - timedelta(hours=TZ_HOURS - 24)
    res = []
    for dt_utc, q in _events_utc(utc_start, utc_end):
        loc = dt_utc + timedelta(hours=TZ_HOURS)
        if loc.year == year and loc.month == month:
            res.append((loc.day + (loc.hour + loc.minute / 60.0) / 24.0, q))
    return res


def month_moon_symbols(year: int, month: int) -> dict[int, str]:
    """{當地日: 符號}，供 Report 1 / Report 2 表格使用。"""
    return {int(x): QUARTER_SYMBOLS[q] for x, q in month_moon_events(year, month)}


if __name__ == '__main__':
    import sys
    y = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    names = {0: '朔', 1: '上弦', 2: '望', 3: '下弦'}
    for m in range(1, 13):
        for x, q in month_moon_events(y, m):
            d = int(x); hh = (x - d) * 24
            print(f'{y}-{m:02d}-{d:02d} {int(hh):02d}:{int(round((hh % 1) * 60)) % 60:02d}  {names[q]}')
