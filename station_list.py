"""
station_list.py — 潮位站一覽表（前言章節，非報表編號）
用法（獨立執行）：
    python station_list.py --year 2026 --html-only
    python station_list.py --year 2026
供 pipeline 呼叫：
    from station_list import generate
    html = generate(conn, year=2026, stid_order=["1516", "1102", ...])
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).parent))
from db import load_env, get_connection

# ──────────────────────────────────────────────────────────────────────────────
# 常數對照表
# ──────────────────────────────────────────────────────────────────────────────
_TYPE_ZH: dict[str, str] = {
    '1': '音波式',
    '2': '音波式',
    '3': '壓力式',
    '4': '雷達式',
}
_TYPE_EN: dict[str, str] = {
    '1': 'Sonic',
    '2': 'Sonic',
    '3': 'Pressure',
    '4': 'Radar',
}
_KIND_ZH: dict[str, str] = {
    '6': '潮位站',
    # '7': '波浪站',
    '9': '水位浮標站',
}
_KIND_EN: dict[str, str] = {
    '6': 'Tide Station',
    # '7': 'Wave/Buoy Station',
    # '9': 'Water_Buoy Station',
}

# ──────────────────────────────────────────────────────────────────────────────
# 資料庫查詢
# ──────────────────────────────────────────────────────────────────────────────
# 出版品收錄測站條件（單一來源；generate_report.py 也 import 這個常數）。
# SQL 內以別名 s 指 st 表；%(year)s 由呼叫端用 cur.execute(sql, {'year': 年份}) 帶入。
# 最後一項：該年度 tidestat 至少有一個月的平均潮位 → 早已停測、當年沒資料的站不列入。
ELIGIBLE_STATION_SQL = """
    s.kind IN ('6', '9')
    AND s.reliable = '1'
    AND s.sponsor = '中央氣象署'
    AND EXISTS (
        SELECT 1 FROM tidestat ts
        WHERE ts.STID  = s.stid
          AND ts.YEAR  = %(year)s
          AND ts.SL    = 'S'
          AND ts.MONTH BETWEEN 1 AND 12
          AND ts.MWL IS NOT NULL
    )
"""

_SQL = """
SELECT
    s.stid,
    s.stid_new,
    s.stnac,
    s.stnae,
    s.location,
    s.latit,
    s.longit,
    s.kind,
    s.type,
    s.sponsor,
    s.sponsore,
    kp.ZSHIFT,
    kp.TGBM,
    kp.TWVD,
    kp.Report
FROM st s
LEFT JOIN (
    SELECT STID, ZSHIFT, TGBM, TWVD, Report
    FROM tiderlkp
    WHERE END IS NULL
) kp ON s.stid = kp.STID
WHERE """ + ELIGIBLE_STATION_SQL + """
ORDER BY CAST(s.stid AS UNSIGNED)
"""

def query_stations(conn, year: int) -> list[dict]:
    """從 DB 取出符合收錄條件的測站（含高程資訊），按 stid 數值排序。"""
    with conn.cursor() as cur:
        cur.execute(_SQL, {'year': year})
        return cur.fetchall()


# ──────────────────────────────────────────────────────────────────────────────
# 格式化輔助
# ──────────────────────────────────────────────────────────────────────────────
def _fmt_elevation(zshift) -> str:
    """ZSHIFT (mm) → 公尺字串，3 位小數；NULL → '尚未公告'。"""
    if zshift is None:
        return '尚未公告'
    return f'{int(zshift) / 1000:.3f}'


def _fmt_tgbm(tgbm, twvd) -> str:
    """
    TGBM 編號；NULL → '尚未公告'。
    TWVD != 'Y' 的站（離島，使用當地 LMSL）加星號標記，對應表格腳注。
    """
    if tgbm is None:
        return '尚未公告'
    star = '' if twvd == 'Y' else '*'
    return f'{tgbm}{star}'


def _is_lmsl(twvd) -> bool:
    """True 表示該站採用當地平均海水面（LMSL），非 TWVD2001。"""
    return twvd is not None and twvd != 'Y'


def _fmt_report_year(report, zshift) -> str:
    """tiderlkp.Report（內政部量測年分，如 '2017'）；高程值尚未公告或無年分 → '—'。"""
    if zshift is None or report is None or not str(report).strip():
        return '—'
    return str(report).strip()


_LOC_MAX_CHARS = 9   # 位置欄每行最多字數（依欄寬 11% 估算；改欄寬時同步調整）
_LOC_PUNCT   = '，,、；;'
_LOC_SUFFIX  = '縣鄉鎮區村里路街巷港'


def _balance_location(text: str, max_chars: int = _LOC_MAX_CHARS) -> list[str]:
    """
    位置文字平均分成 ceil(n / max_chars) 行（WeasyPrint 不支援 text-wrap: balance）。
    斷點在理想位置 ±3 字內，優先選標點之後 / 左括號之前，其次行政區或路名字尾之後。
    """
    text = (text or '').strip()
    n = len(text)
    if n <= max_chars:
        return [text]
    k = -(-n // max_chars)                      # 行數 = ceil(n / max_chars)
    cuts: list[int] = []
    prev = 0
    for i in range(1, k):
        ideal = round(n * i / k)
        lines_left = k - i                      # 此斷點之後還要排幾行
        best, best_key = ideal, None
        for p in range(ideal - 3, ideal + 4):
            if not (prev < p < n):
                continue
            if text[p - 1] == '第' or (text[p - 1].isascii() and text[p - 1].isalnum()
                                       and text[p].isascii() and text[p].isalnum()):
                continue                        # 不在「第」之後、或英數字串中間斷開
            if p - prev > max_chars or (n - p) > max_chars * lines_left:
                continue                        # 會造成某行過長 → 不採用
            if text[p - 1] in _LOC_PUNCT or text[p] in '(（':
                score = 3
            elif text[p - 1] in ')）':
                score = 2
            elif text[p - 1] in _LOC_SUFFIX:
                score = 1
            else:
                score = 0
            key = (-score, abs(p - ideal))
            if best_key is None or key < best_key:
                best, best_key = p, key
        cuts.append(best)
        prev = best
    bounds = [0] + cuts + [n]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


# ──────────────────────────────────────────────────────────────────────────────
# 資料處理
# ──────────────────────────────────────────────────────────────────────────────
def process_stations(
    rows: list[dict],
    stid_order: list[str] | None = None,
) -> list[dict]:
    """
    將 DB raw rows 轉換成模板可直接使用的 dict 清單。
    若提供 stid_order（YAML 站碼序列）：只保留其內的站並依該順序排列，
    使一覽表與內文報表的測站一致；None 時列出全部符合條件的站。
    """
    if stid_order:
        idx = {s: i for i, s in enumerate(stid_order)}
        have = {str(r['stid']) for r in rows}
        missing = [s for s in stid_order if s not in have]
        if missing:
            print(f'[station_list] ⚠ 下列站碼查無符合條件的 st 資料，未列入一覽表：{missing}')
        rows = sorted(
            (r for r in rows if str(r['stid']) in idx),
            key=lambda r: idx[str(r['stid'])],
        )

    out: list[dict] = []
    has_lmsl = False   # 是否有任何一站用 LMSL（決定是否顯示腳注）
    for i, r in enumerate(rows, start=1):
        t   = str(r.get('type') or '')
        k   = str(r.get('kind') or '')
        lmsl = _is_lmsl(r.get('TWVD'))
        if lmsl:
            has_lmsl = True
        out.append({
            'seq':        i,
            'stid':       str(r['stid']),
            'stid_new':   str(r.get('stid_new') or ''),
            'name_zh':    str(r.get('stnac') or ''),
            'name_en':    str(r.get('stnae') or ''),
            'location':   str(r.get('location') or ''),
            'location_lines': _balance_location(str(r.get('location') or '')),
            'lat':        str(r.get('latit')  or ''),
            'lon':        str(r.get('longit') or ''),
            'elevation':  _fmt_elevation(r.get('ZSHIFT')),
            'tgbm':       _fmt_tgbm(r.get('TGBM'), r.get('TWVD')),
            'report_year': _fmt_report_year(r.get('Report'), r.get('ZSHIFT')),
            'sponsor_zh': str(r.get('sponsor')  or ''),
            'sponsor_en': str(r.get('sponsore') or ''),
            'type_zh':    'GNSS' if (k == '9' and t == '1') else _TYPE_ZH.get(t, '—'),
            'type_en':    'GNSS' if (k == '9' and t == '1') else _TYPE_EN.get(t, '—'),
            'kind_zh':    _KIND_ZH.get(k, ''),
            'kind_en':    _KIND_EN.get(k, ''),
            'is_lmsl':    lmsl,   # 是否需顯示 * 腳注
        })
    return out, has_lmsl


# ──────────────────────────────────────────────────────────────────────────────
# HTML 渲染
# ──────────────────────────────────────────────────────────────────────────────
def render_html(
    stations: list[dict],
    has_lmsl: bool,
    year: int,
    roc_year: int,
) -> str:
    tmpl_dir = Path(__file__).parent / 'templates'
    env  = Environment(loader=FileSystemLoader(str(tmpl_dir)), autoescape=True)
    tmpl = env.get_template('station_list.html.j2')
    return tmpl.render(
        stations  = stations,
        has_lmsl  = has_lmsl,
        year      = year,
        roc_year  = roc_year,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 對外介面（供 pipeline 呼叫）
# ──────────────────────────────────────────────────────────────────────────────
def generate(
    conn,
    year: int,
    stid_order: list[str] | None = None,
) -> str:
    """
    查詢 DB → 處理資料 → 回傳 HTML 字串。
    stid_order: YAML stations.list 的值；None 時按 DB 預設排序（stid 數值）。
    """
    rows = query_stations(conn, year)
    stations, has_lmsl = process_stations(rows, stid_order)
    return render_html(stations, has_lmsl, year, year - 1911)


# ──────────────────────────────────────────────────────────────────────────────
# 獨立執行
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description='潮位站一覽表產生器')
    parser.add_argument('--year',      required=True, type=int)
    parser.add_argument('--html-only', action='store_true', help='只輸出 HTML，略過 PDF')
    parser.add_argument('--debug-env', action='store_true')
    args = parser.parse_args()

    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        html     = generate(conn, args.year)
        out_dir  = Path(__file__).parent / 'output'
        out_dir.mkdir(exist_ok=True)
        html_path = out_dir / f'station_list_{args.year}.html'
        html_path.write_text(html, encoding='utf-8')
        print(f'[station_list] HTML → {html_path.resolve()}')

        if args.html_only:
            print('[station_list] ✓ 完成（HTML only）')
            return

        try:
            from weasyprint import HTML as WP
        except ImportError:
            print('⚠ WeasyPrint 未安裝。請執行：pip install WeasyPrint')
            print('  或改用 --html-only 先在瀏覽器確認版面。')
            sys.exit(1)

        pdf_path = out_dir / f'station_list_{args.year}.pdf'
        WP(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(pdf_path))
        print(f'[station_list] PDF  → {pdf_path.resolve()}')
        print('[station_list] ✓ 完成')

    finally:
        conn.close()


if __name__ == '__main__':
    main()
