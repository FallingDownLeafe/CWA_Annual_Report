"""
逐月統計年報（Report 3）產生腳本
用法：python report3_annual_stats.py --stid 1516 --year 2026
      python report3_annual_stats.py --stid 1516 --year 2026 --html-only   ← 快速預覽，不跑 WeasyPrint
      python report3_annual_stats.py --stid 1516 --year 2026 --debug-env   ← 印出 .env 讀取詳情
輸出：output/report3_{stid}_{year}.html  +  .pdf（html-only 時略過 PDF）
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).parent))
from db import load_env, get_connection

# ──────────────────────────────────────────────────────────────────────────────
# 測站基本資料（暫時手動維護；確認 mrbank 有無測站主檔資料表後可改成 DB 查詢）
# ──────────────────────────────────────────────────────────────────────────────
STATION_INFO: dict[str, dict] = {
    '1516': {
        'name_zh':       '基隆',
        'name_en':       'Keelung',
        'lat':           "25°09'N",
        'lon':           "121°44'E",
        'location_desc': '',
        'benchmark_id':  '',        # 潮高參考水準點編號，如 TG997
    },
    # 新增測站時在此加一筆：
    # 'STID': {'name_zh': '...', 'name_en': '...', 'lat': '...', 'lon': '...',
    #          'location_desc': '...', 'benchmark_id': '...'},
}

MONTHS_ZH = ['一月','二月','三月','四月','五月','六月',
             '七月','八月','九月','十月','十一月','十二月']
MONTHS_EN = ['Jan','Feb','Mar','Apr','May','Jun',
             'Jul','Aug','Sep','Oct','Nov','Dec']


# ──────────────────────────────────────────────────────────────────────────────
# DB 查詢
# ──────────────────────────────────────────────────────────────────────────────
def query_tidestat(conn, stid: str, year: int) -> list[dict]:
    """
    取 tidestat SL='S'，每月保留 LAST_UPDATETIME 最新那筆（防呆重複列）。
    QC='Q'（正常）與 QC='%'（資料量不足）都取出，輸出時以 * 標記後者。
    """
    sql = """
        SELECT t.*
        FROM tidestat t
        INNER JOIN (
            SELECT STID, YEAR, MONTH, SL,
                   MAX(LAST_UPDATETIME) AS latest_update
            FROM tidestat
            WHERE STID  = %s
              AND YEAR   = %s
              AND SL     = 'S'
              AND MONTH BETWEEN 0 AND 12
            GROUP BY STID, YEAR, MONTH, SL
        ) d ON  t.STID            = d.STID
            AND t.YEAR            = d.YEAR
            AND t.MONTH           = d.MONTH
            AND t.SL              = d.SL
            AND t.LAST_UPDATETIME = d.latest_update
        WHERE t.STID = %s
          AND t.YEAR = %s
          AND t.SL   = 'S'
        ORDER BY t.MONTH
    """
    with conn.cursor() as cur:
        cur.execute(sql, (stid, year, stid, year))
        return cur.fetchall()


# ──────────────────────────────────────────────────────────────────────────────
# 格式化輔助
# ──────────────────────────────────────────────────────────────────────────────
def _to_cm(val) -> int | None:
    """mm（DB 儲存）→ cm 四捨五入整數；None 原樣回傳。"""
    return None if val is None else round(int(val) / 10)


def fmt_val(val, qc: str) -> str:
    """統計值欄位：None→空白；QC='%'→數字+*；其他→數字。"""
    cm = _to_cm(val)
    if cm is None:
        return ''
    return f'{cm}*' if qc == '%' else str(cm)


def fmt_no(val) -> str:
    """高低潮次數：整數，不換算。"""
    return '' if val is None else str(int(val))


def fmt_lunar(mmdd) -> str:
    """
    HHWLD/LLWLD 農曆日期（DB 格式 MMDD，decimal int）→ 'M月D日'
    例：626 → '6月26日'，1203 → '12月3日'
    """
    if mmdd is None:
        return ''
    v = int(mmdd)
    return f'{v // 100}月{v % 100}日'


def fmt_dt_day(dt) -> str:
    """datetime → 日（整數字串）；None→空白。"""
    if dt is None:
        return ''
    if isinstance(dt, str):
        dt = datetime.strptime(dt[:19], '%Y-%m-%d %H:%M:%S')
    return str(dt.day)


def fmt_dt_time(dt) -> str:
    """datetime → 'HH:MM'；None→空白。"""
    if dt is None:
        return ''
    if isinstance(dt, str):
        dt = datetime.strptime(dt[:19], '%Y-%m-%d %H:%M:%S')
    return dt.strftime('%H:%M')


# ──────────────────────────────────────────────────────────────────────────────
# 資料處理
# ──────────────────────────────────────────────────────────────────────────────
_FIELDS = ['qc','MWL','NO','MHWL','MLWL','MHAT','MLAT','HAT','LAT',
           'HHWL_day','HHWL_lunar','HHWL_time','HHWL',
           'LLWL_day','LLWL_lunar','LLWL_time','LLWL',
           'MR','MAR','MATR','MTR']
_EMPTY = {k: '' for k in _FIELDS}


def _proc(r: dict) -> dict:
    qc = str(r.get('QC', 'Q'))
    return {
        'qc':          qc,
        'MWL':         fmt_val(r.get('MWL'),  qc),
        'NO':          fmt_no(r.get('NO')),
        'MHWL':        fmt_val(r.get('MHWL'), qc),
        'MLWL':        fmt_val(r.get('MLWL'), qc),
        'MHAT':        fmt_val(r.get('MHAT'), qc),
        'MLAT':        fmt_val(r.get('MLAT'), qc),
        'HAT':         fmt_val(r.get('HAT'),  qc),
        'LAT':         fmt_val(r.get('LAT'),  qc),
        'HHWL_day':    fmt_dt_day(r.get('HHWLT')),
        'HHWL_lunar':  fmt_lunar(r.get('HHWLD')),
        'HHWL_time':   fmt_dt_time(r.get('HHWLT')),
        'HHWL':        fmt_val(r.get('HHWL'), qc),
        'LLWL_day':    fmt_dt_day(r.get('LLWLT')),
        'LLWL_lunar':  fmt_lunar(r.get('LLWLD')),
        'LLWL_time':   fmt_dt_time(r.get('LLWLT')),
        'LLWL':        fmt_val(r.get('LLWL'), qc),
        'MR':          fmt_val(r.get('MR'),   qc),
        'MAR':         fmt_val(r.get('MAR'),  qc),
        'MATR':        fmt_val(r.get('MATR'), qc),
        'MTR':         fmt_val(r.get('MTR'),  qc),
    }


def process_rows(raw: list[dict]) -> tuple[list[dict], dict | None]:
    """
    回傳 (monthly_rows×12, annual_row|None)。
    缺月份以空白列補齊（例如年度尚未結束）；MONTH=0 是年度彙整列。
    """
    by_month = {int(r['MONTH']): r for r in raw}

    monthly_rows = []
    for m in range(1, 13):
        row = _proc(by_month[m]) if m in by_month else dict(_EMPTY)
        row['month_zh'] = MONTHS_ZH[m - 1]
        row['month_en'] = MONTHS_EN[m - 1]
        monthly_rows.append(row)

    annual_row = _proc(by_month[0]) if 0 in by_month else None
    return monthly_rows, annual_row


# ──────────────────────────────────────────────────────────────────────────────
# HTML 渲染
# ──────────────────────────────────────────────────────────────────────────────
def render_html(monthly_rows, annual_row, stid: str, year: int,
                station: dict | None = None) -> str:
    tmpl_dir = Path(__file__).parent / 'templates'
    env = Environment(loader=FileSystemLoader(str(tmpl_dir)), autoescape=False)
    tmpl = env.get_template('report3.html.j2')

    if station is None:
        station = STATION_INFO.get(stid, {
            'name_zh': stid, 'name_en': '',
            'lat': '', 'lon': '', 'location_desc': '', 'benchmark_id': '',
        })

    return tmpl.render(
        station      = station,
        stid         = stid,
        year         = year,
        roc_year     = year - 1911,
        monthly_rows = monthly_rows,
        annual_row   = annual_row,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 對外介面（供 generate_report.py pipeline 呼叫）
# ──────────────────────────────────────────────────────────────────────────────
def generate(conn, stid: str, year: int, station_info: dict | None = None) -> str:
    """
    查詢 tidestat → 處理 → 回傳 HTML 字串。
    station_info 若由 pipeline 傳入（鍵：name_zh/name_en/lat/lon/location/benchmark_id），
    會轉成模板使用的 location_desc；None 時使用本檔 STATION_INFO。
    """
    raw = query_tidestat(conn, stid, year)
    if not raw:
        raise ValueError(f'[report3] 查無資料：STID={stid}，YEAR={year}')
    monthly_rows, annual_row = process_rows(raw)

    station = None
    if station_info:
        station = dict(station_info)
        station.setdefault('location_desc', station_info.get('location', ''))
    return render_html(monthly_rows, annual_row, stid, year, station)


# ──────────────────────────────────────────────────────────────────────────────
# 進入點
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description='逐月統計年報 PDF 產生器')
    parser.add_argument('--stid',      required=True,        help='測站代碼，例：1516')
    parser.add_argument('--year',      required=True, type=int, help='西元年，例：2026')
    parser.add_argument('--html-only', action='store_true',  help='只輸出 HTML，略過 PDF（快速預覽用）')
    parser.add_argument('--debug-env', action='store_true',  help='印出 .env 讀取詳情')
    args = parser.parse_args()

    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        print(f'[report3] 查詢 tidestat：STID={args.stid}，YEAR={args.year}')
        raw = query_tidestat(conn, args.stid, args.year)
        print(f'[report3] 取得 {len(raw)} 筆（含年度彙整列）')

        if not raw:
            print('⚠ 查無資料，請確認 STID 和 YEAR 是否正確。')
            sys.exit(1)

        monthly_rows, annual_row = process_rows(raw)
        html = render_html(monthly_rows, annual_row, args.stid, args.year)

        out_dir = Path(__file__).parent / 'output'
        out_dir.mkdir(exist_ok=True)
        stem = f'report3_{args.stid}_{args.year}'

        html_path = out_dir / f'{stem}.html'
        html_path.write_text(html, encoding='utf-8')
        print(f'[report3] HTML → {html_path.resolve()}')

        if args.html_only:
            print('[report3] ✓ 完成（HTML only）')
            return

        try:
            from weasyprint import HTML as WP
        except ImportError:
            print('⚠ WeasyPrint 未安裝。請執行：pip install WeasyPrint')
            print('  或改用 --html-only 先在瀏覽器確認版面。')
            sys.exit(1)

        pdf_path = out_dir / f'{stem}.pdf'
        print('[report3] 轉換 PDF 中（首次可能需要 10–30 秒）…')
        WP(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(pdf_path))
        print(f'[report3] PDF  → {pdf_path.resolve()}')
        print('[report3] ✓ 完成')

    finally:
        conn.close()


if __name__ == '__main__':
    main()