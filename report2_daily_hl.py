"""
report2_daily_hl.py — 逐日高低潮月報（Report 2）

用法：
    python report2_daily_hl.py --stid 1516 --year 2026
    python report2_daily_hl.py --stid 1516 --year 2026 --html-only
    python report2_daily_hl.py --stid 1516 --year 2026 --month 7
    python report2_daily_hl.py --stid 1516 --year 2026 --debug-env

輸出：
    output/report2_{stid}_{year}.html           全年 12 個月（各一頁）
    output/report2_{stid}_{year}_m{mm}.html     --month 指定時
    output/report2_{stid}_{year}.pdf

依賴套件：
    pip install pymysql jinja2 lunardate astral
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

try:
    from lunardate import LunarDate
    _LUNARDATE_OK = True
except ImportError:
    _LUNARDATE_OK = False

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
        'name_zh': '基隆',   'name_en': 'Keelung',
        'lat': "25°09'N",   'lon': "121°44'E",
        'location': '基隆港', 'benchmark_id': 'TG997', 'sponsor': '中央氣象署',
    },
}

MONTHS_ZH = ['一月', '二月', '三月', '四月', '五月', '六月',
              '七月', '八月', '九月', '十月', '十一月', '十二月']
MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

_MOON_ICONS: list[tuple[float, str]] = [
    (0.0,  '●'), (7.0, '◐'), (14.0, '○'), (21.0, '◑'),
]

# Rule b：當日有效高低潮筆數上限（> 此值 → 整日空白）
# Step-2 規劃文件：COUNT > 4 → 整列空白（疑義：待與同事確認）
_RULE_B_MAX: int = 4


# ──────────────────────────────────────────────────────────────────────────────
# 月相計算（與 report4 相同邏輯，複製以維持獨立可執行）
# ──────────────────────────────────────────────────────────────────────────────
def _month_moon_phases(year: int, month: int) -> dict[int, str]:
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
    if not _LUNARDATE_OK:
        return None, None
    try:
        ld = LunarDate.fromSolarDate(year, month, day)
        return int(ld.month), int(ld.day)
    except Exception:
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# 格式化輔助
# ──────────────────────────────────────────────────────────────────────────────
def _to_cm(val) -> int | None:
    return None if val is None else round(int(val) / 10.0)


def _fmt_day(dt) -> str:
    """datetime → 日（整數字串），None → ''"""
    if dt is None:
        return ''
    if isinstance(dt, str):
        dt = datetime.strptime(dt[:19], '%Y-%m-%d %H:%M:%S')
    return str(dt.day)


def _fmt_time(dt) -> str:
    """datetime → 'HH:MM'，None → ''"""
    if dt is None:
        return ''
    if isinstance(dt, str):
        dt = datetime.strptime(dt[:19], '%Y-%m-%d %H:%M:%S')
    return dt.strftime('%H:%M')


def _hl_time(hour, minute) -> str:
    """hour/minute → 'HH:MM'"""
    return f'{int(hour):02d}:{int(minute):02d}'


# ──────────────────────────────────────────────────────────────────────────────
# DB 查詢 1：tidehl 逐日高低潮
# ──────────────────────────────────────────────────────────────────────────────
def query_month_hl(
    conn,
    stid: str,
    year: int,
    month: int,
) -> dict[int, list[dict]]:
    """
    回傳 {day: [records]}，每筆 record 含 HORL, HEIGHT, HOUR, MINUTE。
    只取 QC='Q'，依 DAY, HOUR, MINUTE 排序。
    """
    sql = """
        SELECT DAY, HOUR, MINUTE, HORL, HEIGHT
        FROM   tidehl
        WHERE  STID  = %s
          AND  YEAR  = %s
          AND  MONTH = %s
          AND  QC    = 'Q'
        ORDER  BY DAY, HOUR, MINUTE
    """
    with conn.cursor() as cur:
        cur.execute(sql, (stid, year, month))
        rows = cur.fetchall()

    by_day: dict[int, list[dict]] = {}
    for r in rows:
        d = int(r['DAY'])
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)
    return by_day


# ──────────────────────────────────────────────────────────────────────────────
# DB 查詢 2：tidestat 月統計（右側面板）
# ──────────────────────────────────────────────────────────────────────────────
def query_month_stats(
    conn,
    stid: str,
    year: int,
    month: int,
) -> dict | None:
    """
    取 tidestat SL='S' 最新一筆（dedup 防重複列）。
    QC='Q' 或 '%' 均取，由上層判斷是否加 * 標記。
    """
    sql = """
        SELECT t.*
        FROM tidestat t
        INNER JOIN (
            SELECT STID, YEAR, MONTH, SL, MAX(LAST_UPDATETIME) AS lu
            FROM   tidestat
            WHERE  STID  = %s AND YEAR  = %s AND MONTH = %s
              AND  SL    = 'S'
            GROUP  BY STID, YEAR, MONTH, SL
        ) d ON  t.STID            = d.STID
            AND t.YEAR            = d.YEAR
            AND t.MONTH           = d.MONTH
            AND t.SL              = d.SL
            AND t.LAST_UPDATETIME = d.lu
        WHERE t.STID  = %s AND t.YEAR  = %s AND t.MONTH = %s AND t.SL = 'S'
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (stid, year, month, stid, year, month))
        row = cur.fetchone()
    return row


# ──────────────────────────────────────────────────────────────────────────────
# 資料處理
# ──────────────────────────────────────────────────────────────────────────────
def _build_blank_row(year: int, month: int, day: int,
                     moon_sym: str,
                     lm: int | None, ld_n: int | None) -> dict:
    return {
        'greg_day': day, 'lunar_month': lm, 'lunar_day': ld_n,
        'moon_sym': moon_sym, 'is_blank': True,
        'h1_time': None, 'h1_height': None,
        'l1_time': None, 'l1_height': None,
        'h2_time': None, 'h2_height': None,
        'l2_time': None, 'l2_height': None,
        'diurnal_range': None,
    }


def _process_month(
    hl_by_day: dict[int, list[dict]],
    year: int,
    month: int,
) -> list[dict]:
    n_days    = calendar.monthrange(year, month)[1]
    moon_syms = _month_moon_phases(year, month)
    rows: list[dict] = []

    for d in range(1, n_days + 1):
        lm, ld_n = _lunar(year, month, d)
        moon_sym  = moon_syms.get(d, '')
        day_recs  = hl_by_day.get(d, [])

        # 無資料 → 空白列
        if not day_recs:
            rows.append(_build_blank_row(year, month, d, moon_sym, lm, ld_n))
            continue

        # Rule b：筆數超限 → 空白列
        if len(day_recs) > _RULE_B_MAX:
            rows.append(_build_blank_row(year, month, d, moon_sym, lm, ld_n))
            continue

        # 依 H / L 分類，並依時間排序
        h_recs = [r for r in day_recs if str(r['HORL']).strip() == 'H']
        l_recs = [r for r in day_recs if str(r['HORL']).strip() == 'L']

        def _t(r):
            return _hl_time(r['HOUR'], r['MINUTE'])

        def _cm(r):
            return _to_cm(r['HEIGHT'])

        h1 = h_recs[0] if len(h_recs) >= 1 else None
        h2 = h_recs[1] if len(h_recs) >= 2 else None
        l1 = l_recs[0] if len(l_recs) >= 1 else None
        l2 = l_recs[1] if len(l_recs) >= 2 else None

        # 潮差：MAX(H) - MIN(L)，各至少需一筆
        all_h_cm = [_cm(r) for r in h_recs if _cm(r) is not None]
        all_l_cm = [_cm(r) for r in l_recs if _cm(r) is not None]
        diurnal = (max(all_h_cm) - min(all_l_cm)) if all_h_cm and all_l_cm else None

        rows.append({
            'greg_day':     d,
            'lunar_month':  lm,
            'lunar_day':    ld_n,
            'moon_sym':     moon_sym,
            'is_blank':     False,
            'h1_time':      _t(h1)  if h1 else None,
            'h1_height':    _cm(h1) if h1 else None,
            'l1_time':      _t(l1)  if l1 else None,
            'l1_height':    _cm(l1) if l1 else None,
            'h2_time':      _t(h2)  if h2 else None,
            'h2_height':    _cm(h2) if h2 else None,
            'l2_time':      _t(l2)  if l2 else None,
            'l2_height':    _cm(l2) if l2 else None,
            'diurnal_range': diurnal,
        })

    return rows


def _proc_stats(raw: dict | None) -> dict | None:
    """將 tidestat 原始列轉換成模板用 dict（cm）。"""
    if raw is None:
        return None
    return {
        'star':      '*' if str(raw.get('QC', '')).strip() == '%' else '',   # QC='%' 資料量不足 2/3
        'msl':       _to_cm(raw.get('MWL')),
        'mhwl':      _to_cm(raw.get('MHWL')),
        'mlwl':      _to_cm(raw.get('MLWL')),
        'mr':        _to_cm(raw.get('MR')),
        'mtr':       _to_cm(raw.get('MTR')),
        'hhwl':      _to_cm(raw.get('HHWL')),
        'hhwl_day':  _fmt_day(raw.get('HHWLT')),
        'hhwl_time': _fmt_time(raw.get('HHWLT')),
        'llwl':      _to_cm(raw.get('LLWL')),
        'llwl_day':  _fmt_day(raw.get('LLWLT')),
        'llwl_time': _fmt_time(raw.get('LLWLT')),
        'hat':       _to_cm(raw.get('HAT')),
        'qc':        str(raw.get('QC', '')),
    }


# ──────────────────────────────────────────────────────────────────────────────
# HTML 渲染
# ──────────────────────────────────────────────────────────────────────────────
def render_html(pages: list[dict], station: dict, year: int) -> str:
    tmpl_dir = Path(__file__).parent / 'templates'
    env  = Environment(loader=FileSystemLoader(str(tmpl_dir)), autoescape=False)
    tmpl = env.get_template('report2.html.j2')
    return tmpl.render(pages=pages, station=station, year=year)


# ──────────────────────────────────────────────────────────────────────────────
# 公開 API
# ──────────────────────────────────────────────────────────────────────────────
def generate(
    conn,
    stid: str,
    year: int,
    station_info: dict | None = None,
    months: list[int] | None = None,
) -> str:
    """
    查詢 DB → 處理資料 → 回傳 HTML 字串（供 pipeline 呼叫）。
    months: 要輸出的月份清單（預設全年 1–12）。
    """
    station        = station_info or STATION_INFO.get(stid, {
        'name_zh': stid, 'name_en': stid,
        'lat': '', 'lon': '', 'location': '', 'benchmark_id': '', 'sponsor': '',
    })
    target_months = months or list(range(1, 13))

    print(f'[report2] 查詢 tidehl + tidestat：STID={stid}，YEAR={year}')
    pages: list[dict] = []

    for m in target_months:
        hl_by_day = query_month_hl(conn, stid, year, m)
        stats_raw = query_month_stats(conn, stid, year, m)
        rows      = _process_month(hl_by_day, year, m)
        stats     = _proc_stats(stats_raw)

        total_valid = sum(
            len(v) for v in hl_by_day.values()
        )
        print(f'[report2]   {MONTHS_ZH[m-1]}：{total_valid} 筆高低潮資料'
              f'，tidestat QC={stats["qc"] if stats else "無"}')

        pages.append({
            'month':    m,
            'month_zh': MONTHS_ZH[m - 1],
            'month_en': MONTHS_EN[m - 1],
            'year':     year,
            'roc_year': year - 1911,
            'rows':     rows,
            'stats':    stats,
        })

    return render_html(pages, station, year)


# ──────────────────────────────────────────────────────────────────────────────
# CLI 進入點
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    if not _LUNARDATE_OK:
        print('[report2] ⚠ lunardate 未安裝。請執行：pip install lunardate')
    if not _ASTRAL_OK:
        print('[report2] ⚠ astral 未安裝。請執行：pip install astral')

    parser = argparse.ArgumentParser(description='逐日高低潮月報（Report 2）產生器')
    parser.add_argument('--stid',      required=True,           help='測站代碼，例：1516')
    parser.add_argument('--year',      required=True, type=int, help='西元年，例：2026')
    parser.add_argument('--month',     type=int, default=None,  help='只輸出指定月份（1–12）')
    parser.add_argument('--html-only', action='store_true',     help='只輸出 HTML，略過 PDF')
    parser.add_argument('--debug-env', action='store_true',     help='印出 .env 讀取詳情')
    args = parser.parse_args()

    if args.month and not (1 <= args.month <= 12):
        print('⚠ --month 必須為 1–12'); sys.exit(1)

    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        months = [args.month] if args.month else None
        html   = generate(conn, args.stid, args.year, months=months)

        out_dir = Path(__file__).parent / 'output'
        out_dir.mkdir(exist_ok=True)
        suffix    = f'_m{args.month:02d}' if args.month else ''
        stem      = f'report2_{args.stid}_{args.year}{suffix}'

        html_path = out_dir / f'{stem}.html'
        html_path.write_text(html, encoding='utf-8')
        print(f'[report2] HTML → {html_path.resolve()}')

        if args.html_only:
            print('[report2] ✓ 完成（HTML only）'); return

        try:
            from weasyprint import HTML as WP
        except ImportError:
            print('⚠ WeasyPrint 未安裝。請執行：pip install WeasyPrint')
            sys.exit(1)

        pdf_path = out_dir / f'{stem}.pdf'
        print('[report2] 轉換 PDF 中…')
        WP(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(pdf_path))
        print(f'[report2] PDF  → {pdf_path.resolve()}')
        print('[report2] ✓ 完成')

    finally:
        conn.close()


if __name__ == '__main__':
    main()
