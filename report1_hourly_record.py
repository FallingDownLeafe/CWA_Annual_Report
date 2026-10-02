"""
report1_hourly_record.py — 逐時潮位紀錄表（Report 1）

用法：
    python report1_hourly_record.py --stid 1516 --year 2026
    python report1_hourly_record.py --stid 1516 --year 2026 --html-only
    python report1_hourly_record.py --stid 1516 --year 2026 --month 7   ← 只輸出指定月份
    python report1_hourly_record.py --stid 1516 --year 2026 --debug-env

輸出：
    output/report1_{stid}_{year}.html           全年 12 頁（每月一頁）
    output/report1_{stid}_{year}_m{mm}.html     --month 指定時
    output/report1_{stid}_{year}.pdf            （html-only 時略過）

依賴套件：
    pip install pymysql jinja2 lunardate astral
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# ── 農曆轉換（選用）──────────────────────────────────────────────────────────
try:
    from lunardate import LunarDate
    _LUNARDATE_OK = True
except ImportError:
    _LUNARDATE_OK = False

# ── 月相計算（選用）──────────────────────────────────────────────────────────
try:
    from astral.moon import phase as _astral_phase
    _ASTRAL_OK = True
except ImportError:
    _ASTRAL_OK = False

sys.path.insert(0, str(Path(__file__).parent))
from db import load_env, get_connection


# ──────────────────────────────────────────────────────────────────────────────
# 常數
# ──────────────────────────────────────────────────────────────────────────────
STATION_INFO: dict[str, dict] = {
    '1516': {
        'name_zh':      '基隆',
        'name_en':      'Keelung',
        'lat':          "25°09'N",
        'lon':          "121°44'E",
        'location':     '基隆港',
        'benchmark_id': 'TG997',
        'sponsor':      '中央氣象署',
    },
    '1116': {
        'name_zh':      '竹圍',
        'name_en':      'Jhuwei',
        'lat':          "25°07'N",
        'lon':          "121°15'E",
        'location':     '桃園竹圍漁港',
        'benchmark_id': 'TG04',
        'sponsor':      '中央氣象署',
    },
}

MONTHS_ZH = ['一月', '二月', '三月', '四月', '五月', '六月',
              '七月', '八月', '九月', '十月', '十一月', '十二月']
MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

_MOON_ICONS: list[tuple[float, str]] = [
    (0.0,  '●'),   # 新月
    (7.0,  '◐'),   # 上弦
    (14.0, '○'),   # 滿月
    (21.0, '◑'),   # 下弦
]

# 日平均最小有效觀測筆數門檻
# 原 PDF 腳注：「少於應觀測次數 2/3」= 24 × 2/3 = 16
# 若確認應改用其他閾值，修改此常數即可。
_DAILY_MEAN_MIN_OBS: int = 16


# ──────────────────────────────────────────────────────────────────────────────
# 月相計算（與 report4 相同邏輯，複製以維持各 Report 獨立可執行）
# ──────────────────────────────────────────────────────────────────────────────
def _month_moon_phases(year: int, month: int) -> dict[int, str]:
    """回傳 {day: symbol}：朔/上弦/望/下弦所在的台灣日期（由 moon.py 精確計算）。"""
    from moon import month_moon_symbols   # moon.py 與本檔放同一資料夾
    return month_moon_symbols(year, month)
    # ↓ 以下為舊 astral 邏輯，已不會執行，可日後刪除
    if not _ASTRAL_OK:
        return {}

    n = calendar.monthrange(year, month)[1]
    daily = [(d, float(_astral_phase(date(year, month, d)))) for d in range(1, n + 1)]

    result: dict[int, str] = {}
    for threshold, symbol in _MOON_ICONS:
        if threshold == 0.0:
            best = min(daily, key=lambda t: min(t[1], 28.0 - t[1]))
            dist = min(best[1], 28.0 - best[1])
        else:
            best = min(daily, key=lambda t: abs(t[1] - threshold))
            dist = abs(best[1] - threshold)
        if dist < 3.5:
            result[best[0]] = symbol

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 農曆轉換
# ──────────────────────────────────────────────────────────────────────────────
def _lunar(year: int, month: int, day: int) -> tuple[int | None, int | None]:
    """
    回傳 (lunar_month, lunar_day)；若 lunardate 未安裝回傳 (None, None)。
    """
    if not _LUNARDATE_OK:
        return None, None
    try:
        ld = LunarDate.fromSolarDate(year, month, day)
        return int(ld.month), int(ld.day)
    except Exception:
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# DB 查詢
# ──────────────────────────────────────────────────────────────────────────────
def query_all_months(
    conn,
    stid: str,
    year: int,
) -> dict[int, dict[tuple[int, int], int]]:
    """
    取 tide6 全年 MIN0 逐時水位（qc='Q' AND MIN0 IS NOT NULL）。
    回傳 {month: {(day, hour): value_cm_int}}
    """
    sql = """
        SELECT MONTH, DAY, HOUR, MIN0
        FROM   tide6
        WHERE  STID = %s
          AND  YEAR = %s
          AND  qc   = 'Q'
          AND  MIN0 IS NOT NULL
        ORDER  BY MONTH, DAY, HOUR
    """
    with conn.cursor() as cur:
        cur.execute(sql, (stid, year))
        rows = cur.fetchall()

    data: dict[int, dict[tuple[int, int], int]] = {m: {} for m in range(1, 13)}
    for r in rows:
        m = int(r['MONTH']); d = int(r['DAY']); h = int(r['HOUR'])
        if 1 <= m <= 12:
            data[m][(d, h)] = round(int(r['MIN0']) / 10.0)   # mm → cm（四捨五入整數）
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 資料處理
# ──────────────────────────────────────────────────────────────────────────────
def _process_month(
    month_data: dict[tuple[int, int], int],
    year: int,
    month: int,
) -> list[dict]:
    """
    將一個月的原始資料轉換成模板 row dict 清單。
    """
    n_days     = calendar.monthrange(year, month)[1]
    moon_syms  = _month_moon_phases(year, month)
    rows: list[dict] = []

    for d in range(1, n_days + 1):
        # 收集 24 個小時的值
        hours: dict[int, int | None] = {}
        valid_count = 0
        valid_sum   = 0
        for h in range(24):
            v = month_data.get((d, h))
            hours[h] = v
            if v is not None:
                valid_count += 1
                valid_sum   += v

        # 日平均
        if valid_count == 0:
            daily_mean = None
            is_sparse  = False       # 全無資料：僅空白，不加 ※
        elif valid_count < _DAILY_MEAN_MIN_OBS:
            daily_mean = None
            is_sparse  = True        # 資料不足 2/3 → ※ 標記
        else:
            daily_mean = round(valid_sum / valid_count)
            is_sparse  = False

        # 農曆
        lm, ld_num = _lunar(year, month, d)

        rows.append({
            'greg_day':    d,
            'lunar_month': lm,
            'lunar_day':   ld_num,
            'moon_sym':    moon_syms.get(d, ''),
            'hours':       hours,
            'daily_mean':  daily_mean,
            'is_sparse':   is_sparse,
            'valid_count': valid_count,
        })

    return rows


# ──────────────────────────────────────────────────────────────────────────────
# HTML 渲染
# ──────────────────────────────────────────────────────────────────────────────
def render_html(
    pages: list[dict],
    station: dict,
    year: int,
) -> str:
    tmpl_dir = Path(__file__).parent / 'templates'
    env  = Environment(loader=FileSystemLoader(str(tmpl_dir)), autoescape=False)
    tmpl = env.get_template('report1.html.j2')
    return tmpl.render(
        pages   = pages,
        station = station,
        year    = year,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 公開 API（供 generate_report.py pipeline 呼叫）
# ──────────────────────────────────────────────────────────────────────────────
def generate(
    conn,
    stid: str,
    year: int,
    station_info: dict | None = None,
    months: list[int] | None = None,
) -> str:
    """
    查詢 DB → 處理資料 → 回傳 HTML 字串（12 個月頁面或指定月份）。

    Parameters
    ----------
    conn          : PyMySQL 連線（DictCursor）
    stid          : 測站舊站碼
    year          : 西元年
    station_info  : {'name_zh','name_en','lat','lon','location',
                     'benchmark_id','sponsor'}；None 時從 STATION_INFO 取
    months        : 要輸出的月份清單，例 [7,8]；None 表示全年 1–12
    """
    station = station_info or STATION_INFO.get(stid, {
        'name_zh': stid, 'name_en': stid,
        'lat': '', 'lon': '', 'location': '',
        'benchmark_id': '', 'sponsor': '',
    })
    target_months = months or list(range(1, 13))

    print(f'[report1] 查詢 tide6：STID={stid}，YEAR={year}')
    all_data = query_all_months(conn, stid, year)
    total    = sum(len(v) for v in all_data.values())
    print(f'[report1] 取得 {total} 筆逐時資料')

    if total == 0:
        raise ValueError(f'[report1] 查無資料：STID={stid}，YEAR={year}')

    pages: list[dict] = []
    for m in target_months:
        rows = _process_month(all_data[m], year, m)
        pages.append({
            'month':    m,
            'month_zh': MONTHS_ZH[m - 1],
            'month_en': MONTHS_EN[m - 1],
            'year':     year,
            'roc_year': year - 1911,
            'rows':     rows,
        })
        print(f'[report1]   {MONTHS_ZH[m-1]}：{len(rows)} 天，'
              f'{sum(r["valid_count"] for r in rows)} 筆有效資料')

    return render_html(pages, station, year)


# ──────────────────────────────────────────────────────────────────────────────
# CLI 進入點
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    if not _LUNARDATE_OK:
        print('[report1] ⚠ lunardate 未安裝（農曆欄位將空白）。'
              '請執行：pip install lunardate')
    if not _ASTRAL_OK:
        print('[report1] ⚠ astral 未安裝（月相符號將略過）。'
              '請執行：pip install astral')

    parser = argparse.ArgumentParser(description='逐時潮位紀錄表（Report 1）產生器')
    parser.add_argument('--stid',      required=True,           help='測站代碼，例：1516')
    parser.add_argument('--year',      required=True, type=int, help='西元年，例：2026')
    parser.add_argument('--month',     type=int, default=None,  help='僅輸出指定月份（1–12）')
    parser.add_argument('--html-only', action='store_true',     help='只輸出 HTML，略過 PDF')
    parser.add_argument('--debug-env', action='store_true',     help='印出 .env 讀取詳情')
    args = parser.parse_args()

    if args.month and not (1 <= args.month <= 12):
        print('⚠ --month 必須為 1–12')
        sys.exit(1)

    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        months = [args.month] if args.month else None
        html   = generate(conn, args.stid, args.year, months=months)

        out_dir = Path(__file__).parent / 'output'
        out_dir.mkdir(exist_ok=True)

        suffix    = f'_m{args.month:02d}' if args.month else ''
        stem      = f'report1_{args.stid}_{args.year}{suffix}'
        html_path = out_dir / f'{stem}.html'
        html_path.write_text(html, encoding='utf-8')
        print(f'[report1] HTML → {html_path.resolve()}')

        if args.html_only:
            print('[report1] ✓ 完成（HTML only）')
            return

        try:
            from weasyprint import HTML as WP
        except ImportError:
            print('⚠ WeasyPrint 未安裝。請執行：pip install WeasyPrint')
            print('  或改用 --html-only 先在瀏覽器確認版面。')
            sys.exit(1)

        pdf_path = out_dir / f'{stem}.pdf'
        print('[report1] 轉換 PDF 中（首次可能需要 10–30 秒）…')
        WP(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(pdf_path))
        print(f'[report1] PDF  → {pdf_path.resolve()}')
        print('[report1] ✓ 完成')

    finally:
        conn.close()


if __name__ == '__main__':
    main()
