"""
report4_tidal_sequences.py — 潮位觀測時序圖（Report 4）

用法：
    python report4_tidal_sequences.py --stid 1436 --year 2025
    python report4_tidal_sequences.py --stid 1436 --year 2025 --html-only
    python report4_tidal_sequences.py --stid 1436 --year 2025 --debug-env

輸出：
    output/report4_{stid}_{year}.html
    output/report4_{stid}_{year}.pdf   （html-only 時略過）

依賴套件：
    pip install pymysql matplotlib numpy astral
    中文字型：優先讀取 assets/NotoSansTC-Regular.ttf；
              或安裝 Microsoft JhengHei（Windows）/ Noto Sans TC（Linux/macOS）
"""
from __future__ import annotations

import argparse
import base64
import calendar
import io
import sys
from datetime import date
from pathlib import Path

# ── matplotlib 必須在 pyplot import 前設定後端 ───────────────────────────────
import matplotlib
matplotlib.use('Agg')          # 非互動後端，伺服器/排程安全
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.font_manager as fm
import numpy as np

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
# 測站資訊：與 report3 格式相同；pipeline 整合後改由 DB st 表動態查詢
STATION_INFO: dict[str, dict] = {
    '1516': {'name_zh': '基隆',   'name_en': 'Keelung',       'lat': "25°09'N", 'lon': "121°44'E"},
    '1436': {'name_zh': '臺中港', 'name_en': 'Taichung Port', 'lat': "24°17'N", 'lon': "120°31'E"},
}

MONTHS_ZH = ['一月', '二月', '三月', '四月', '五月', '六月',
              '七月', '八月', '九月', '十月', '十一月', '十二月']
MONTHS_EN = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# 月相：(農曆天數閾值, 顯示符號, 圖例說明)
_MOON_ICONS: list[tuple[float, str, str]] = [
    (0.0,  '●', '新月 New Moon'),
    (7.0,  '◐', '上弦 First Quarter Moon'),
    (14.0, '○', '滿月 Full Moon'),
    (21.0, '◑', '下弦 Third Quarter Moon'),
]

# 色彩（與 HTML 版 report3 一致）
_FILL_COLOR = '#2E86C1'     # 潮位填充藍
_LINE_COLOR = '#1A5276'     # 曲線深藍
_GRID_COLOR = '#D5D8DC'     # 格線淡灰
_TEXT_COLOR = '#1D3557'     # 標題/標籤文字
_MOON_COLOR = '#2C3E50'     # 月相符號

# x 軸刻度候選（每 5 天，依月份天數過濾）
_XTICK_CANDS = [1, 5, 10, 15, 20, 25, 30]


# ──────────────────────────────────────────────────────────────────────────────
# 字型設定
# ──────────────────────────────────────────────────────────────────────────────
_fonts_ready = False


def _setup_matplotlib_fonts() -> None:
    """
    設定 matplotlib 中文字型（執行一次即可）。
    搜尋順序：assets/ TTF → 系統字型 → 降級為 sans-serif（中文顯示□，但不中斷）。
    """
    plt.rcParams['axes.unicode_minus'] = False   # 負號不使用 unicode 減號，避免方塊

    # 1. 專案 assets/ 目錄
    for fname in ('NotoSansTC-Regular.ttf', 'NotoSerifTC-Regular.ttf',
                  'NotoSansTC-Medium.ttf'):
        ttf = Path(__file__).parent / 'assets' / fname
        if ttf.exists():
            fm.fontManager.addfont(str(ttf))
            prop = fm.FontProperties(fname=str(ttf))
            plt.rcParams['font.family'] = [prop.get_name(), 'DejaVu Sans']
            return

    # 2. 已知系統字型（依平台優先順序）
    available = {f.name for f in fm.fontManager.ttflist}
    for cname in ('Microsoft JhengHei',   # Windows 正體中文
                  'PingFang TC',           # macOS
                  'Noto Sans TC',          # Linux / 跨平台
                  'WenQuanYi Micro Hei',   # Linux
                  'Source Han Sans TC'):
        if cname in available:
            plt.rcParams['font.family'] = [cname, 'DejaVu Sans']
            return

    # 3. 降級
    print('[report4] ⚠ 未找到中文字型，中文標籤將顯示為方塊。'
          '請將 NotoSansTC-Regular.ttf 放入 assets/ 目錄。')


def _ensure_fonts() -> None:
    global _fonts_ready
    if not _fonts_ready:
        _setup_matplotlib_fonts()
        _fonts_ready = True


# ──────────────────────────────────────────────────────────────────────────────
# DB 查詢
# ──────────────────────────────────────────────────────────────────────────────
def query_tide6(
    conn,
    stid: str,
    year: int,
) -> dict[int, dict[tuple[int, int], float]]:
    """
    取 tide6 MIN0 逐時水位（qc='Q' AND MIN0 IS NOT NULL）。
    回傳 {month: {(day, hour): water_level_cm}}
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

    data: dict[int, dict[tuple[int, int], float]] = {m: {} for m in range(1, 13)}
    for r in rows:
        m = int(r['MONTH'])
        d = int(r['DAY'])
        h = int(r['HOUR'])
        if 1 <= m <= 12:
            data[m][(d, h)] = int(r['MIN0']) / 10.0   # mm → cm

    return data


# ──────────────────────────────────────────────────────────────────────────────
# 月相計算
# ──────────────────────────────────────────────────────────────────────────────
def _month_moon_phases(year: int, month: int) -> list[tuple[float, str]]:
    """回傳 [(day_x, symbol)]；day_x = 台灣時間的「日 + 時/24」（由 moon.py 精確計算）。"""
    from moon import month_moon_events, QUARTER_SYMBOLS   # moon.py 與本檔同資料夾
    return [(x, QUARTER_SYMBOLS[q]) for x, q in month_moon_events(year, month)]
    # ↓ 以下為舊 astral 邏輯，已不會執行，可日後刪除
    if not _ASTRAL_OK:
        return []

    n = calendar.monthrange(year, month)[1]
    # 每日月相值（0–28，農曆天數）
    daily: list[tuple[int, float]] = [
        (d, float(_astral_phase(date(year, month, d))))
        for d in range(1, n + 1)
    ]

    results: list[tuple[float, str]] = []
    for threshold, symbol, _ in _MOON_ICONS:
        if threshold == 0.0:
            # 新月：phase ≈ 0 或 ≈ 28（循環邊界）
            best = min(daily, key=lambda t: min(t[1], 28.0 - t[1]))
            dist = min(best[1], 28.0 - best[1])
        else:
            best = min(daily, key=lambda t: abs(t[1] - threshold))
            dist = abs(best[1] - threshold)

        if dist < 3.5:   # 月相事件確實落在本月（< 半個月相週期的一半）
            results.append((float(best[0]), symbol))

    return sorted(results, key=lambda t: t[0])


# ──────────────────────────────────────────────────────────────────────────────
# 時間序列陣列建構
# ──────────────────────────────────────────────────────────────────────────────
def _build_series(
    month_data: dict[tuple[int, int], float],
    year: int,
    month: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    建構 (x, y) 連續陣列；缺測位置填 NaN（matplotlib fill_between 自動留白）。
    x = day + hour/24（1 起算連續浮點）；y = cm。
    """
    n = calendar.monthrange(year, month)[1]
    xs: list[float] = []
    ys: list[float] = []
    for d in range(1, n + 1):
        for h in range(24):
            xs.append(d + h / 24.0)
            ys.append(month_data.get((d, h), np.nan))
    return np.array(xs, dtype=float), np.array(ys, dtype=float)


# ──────────────────────────────────────────────────────────────────────────────
# 繪圖核心
# ──────────────────────────────────────────────────────────────────────────────
def create_sequence_figure(
    data: dict[int, dict[tuple[int, int], float]],
    stid: str,
    year: int,
    station: dict,
) -> bytes:
    """
    繪製 12 個月面板潮位時序圖，回傳 PNG bytes。
    figure 固定為 A4 portrait 尺寸，dpi=150。
    """
    _ensure_fonts()

    # ── 全年共用 y 軸範圍 ──────────────────────────────────────────────────
    all_vals = [v for m in data.values() for v in m.values()]
    if all_vals:
        v_min, v_max = min(all_vals), max(all_vals)
        span = v_max - v_min
        pad  = max(span * 0.10, 30.0)
        # y 刻度間距：依潮差大小自動選 50/100/200
        y_step = 200 if span > 500 else (100 if span > 250 else 50)
        y_lo = float(np.floor((v_min - pad) / y_step) * y_step)
        y_hi = float(np.ceil( (v_max + pad) / y_step) * y_step)
    else:
        y_lo, y_hi, y_step = -200.0, 400.0, 100

    # ── 建立圖形 ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(8.27, 11.69), dpi=150)
    fig.patch.set_facecolor('white')

    # 12 面板（6 列 × 2 欄），在標題/圖例留白後置中
    gs = gridspec.GridSpec(
        6, 2,
        figure=fig,
        hspace=0.28,      # 列間距（面板高度比例）
        wspace=0.22,      # 欄間距（面板寬度比例）
        left=0.09,        # 左外邊距（留給月份標籤）
        right=0.91,       # 右外邊距（留給月份標籤）
        top=0.915,        # 上邊距（留給標題）
        bottom=0.062,     # 下邊距（留給圖例）
    )

    # ── 各月面板 ─────────────────────────────────────────────────────────
    for mi in range(12):
        row   = mi % 6        # 面板行（0–5）
        col   = mi // 6       # 面板欄（0=Jan–Jun，1=Jul–Dec）
        month = mi + 1
        n_days = calendar.monthrange(year, month)[1]

        ax = fig.add_subplot(gs[row, col])
        x, y = _build_series(data[month], year, month)

        # 只畫折線（省印刷墨水；NaN 缺測段自動斷線）
        ax.plot(x, y, color=_FILL_COLOR, linewidth=0.5)

        # Y 軸設定
        ax.set_ylim(y_lo, y_hi)
        yticks = np.arange(
            float(np.ceil(y_lo / y_step) * y_step),
            y_hi + y_step * 0.5,
            y_step,
        )
        ax.set_yticks(yticks)
        ax.tick_params(axis='y', labelsize=4.5, pad=1.0, length=2, width=0.5)
        # 右欄 y 軸標籤移到右側
        if col == 1:
            ax.yaxis.tick_right()
            ax.yaxis.set_label_position('right')

        # X 軸設定
        ax.set_xlim(0.5, n_days + 0.5)
        ax.set_xticks([t for t in _XTICK_CANDS if t <= n_days])
        ax.tick_params(axis='x', labelsize=4.5, pad=1.0, length=2, width=0.5)

        # 水平格線
        ax.yaxis.grid(True, color=_GRID_COLOR, linewidth=0.35, linestyle='-')
        ax.set_axisbelow(True)
        ax.xaxis.grid(False)

        # # 邊框細線
        # for sp in ax.spines.values():
        #     sp.set_linewidth(0.45)
        #     sp.set_color('#95a5a6')

        # 邊框細線（確保四邊框線，尤其是頂部天花板都有顯示）
        for sp_name, sp in ax.spines.items():
            sp.set_visible(True)          # 確保每一邊的邊框都顯示出來
            sp.set_linewidth(0.45)
            sp.set_color('#95a5a6')

        # ── 月份標籤（對應外側，垂直旋轉）───────────────────────────
        zh_label = MONTHS_ZH[mi]
        en_label = MONTHS_EN[mi]
        if col == 0:
            # 左欄：中文在上、英文在下，從下往上讀（rotation=90）
            ax.text(
                -0.12, 0.5, f'{zh_label}\n{en_label}',
                transform=ax.transAxes,
                ha='center', va='center',
                rotation=90, fontsize=6.5, fontweight='bold', color=_TEXT_COLOR,
            )
        else:
            # 右欄：英文在上、中文在下，從上往下讀（rotation=-90）
            ax.text(
                1.12, 0.5, f'{en_label}\n{zh_label}',
                transform=ax.transAxes,
                ha='center', va='center',
                rotation=-90, fontsize=6.5, fontweight='bold', color=_TEXT_COLOR,
            )

        # # ── 月相符號（面板頂端，依日期 x 位置定位）──────────────────
        # moon_phases = _month_moon_phases(year, month)
        # sym_y = y_hi - (y_hi - y_lo) * 0.06   # 距頂端 6% 高度處
        # for day_x, sym in moon_phases:
        #     ax.text(
        #         day_x, sym_y, sym,
        #         ha='center', va='center',
        #         fontsize=7.5, color=_MOON_COLOR, zorder=6,
        #         fontfamily='DejaVu Sans',   # 四個符號同字型，高度才會一致
        #     )
        
        # ── 月相符號（改用原生向量 Marker，100% 幾何物理對齊）──────────
        
        MARKER_MAP = {
            '●': {'fillstyle': 'full',  'mfc': _MOON_COLOR, 'mec': _MOON_COLOR},
            '◐': {'fillstyle': 'left',  'mfc': _MOON_COLOR, 'mec': _MOON_COLOR, 'mfcalt': 'white'},
            '○': {'fillstyle': 'none',  'mfc': 'none',       'mec': _MOON_COLOR},
            '◑': {'fillstyle': 'right', 'mfc': _MOON_COLOR, 'mec': _MOON_COLOR, 'mfcalt': 'white'},
        }

        moon_phases = _month_moon_phases(year, month)
        # sym_y = y_hi - (y_hi - y_lo) * 0.06   # 距頂端 6% 高度處
        sym_y = y_hi - (y_hi - y_lo) * 0.045

        # 給不同符號微調尺寸（光學重量補償）
        MARKER_SIZE_MAP = {
            '●': 4.0,  # 實心黑稍微縮小到3.6（減輕重量感）
            '◐': 4.0,  # 半半正常
            '◑': 4.0,  # 半半正常
            '○': 4.0,  # 空心白稍微放大到4.3（增加存在感）
        }

        for day_x, sym in moon_phases:
            st = MARKER_MAP.get(sym, MARKER_MAP['●'])
            msize = MARKER_SIZE_MAP.get(sym, 4.0)
            ax.plot(
                day_x, sym_y,
                marker='o',
                # markersize=3.8,                       # 圓形大小（可依視覺自由微調）
                markersize=msize,                    # 使用微調後的尺寸
                fillstyle=st['fillstyle'],
                markerfacecolor=st['mfc'],
                markeredgecolor=st['mec'],
                markerfacecoloralt=st.get('mfcalt', 'white'),
                markeredgewidth=0.5,
                zorder=6
            )

    # ── 標題區（fig.text，不佔面板空間）─────────────────────────────────
    name_zh  = station.get('name_zh', stid)
    name_en  = station.get('name_en', stid)
    lat      = station.get('lat', '')
    lon      = station.get('lon', '')
    roc_year = year - 1911

    fig.text(
        0.50, 0.978,
        f'{name_zh}潮位站潮位觀測時序圖',
        ha='center', va='top',
        fontsize=13, fontweight='bold', color=_TEXT_COLOR,
    )
    fig.text(
        0.50, 0.958,
        f'Tidal Observation Sequences at {name_en}',
        ha='center', va='top',
        fontsize=8, color=_TEXT_COLOR,
    )
    fig.text(
        0.09, 0.945,
        f'民國{roc_year}年（{year}）',
        ha='left', va='top',
        fontsize=7.0, color=_TEXT_COLOR,
    )
    if lat or lon:
        fig.text(
            0.91, 0.945,
            f'GMT＋8:00\n{lat}　{lon}',
            ha='right', va='top',
            fontsize=6.0, color=_TEXT_COLOR, linespacing=1.5,
        )

    # ── 圖例區 ───────────────────────────────────────────────────────────
    fig.text(
        0.50, 0.050,
        '日　期　date',
        ha='center', va='center',
        fontsize=6.5, color=_TEXT_COLOR,
    )
    # 符號固定 DejaVu Sans、說明用中文字型，各自定位（x 為圖寬比例）
    legend_x = [0.16, 0.33, 0.53, 0.68]
    for x0, (_, sym, desc) in zip(legend_x, _MOON_ICONS):
        fig.text(
            x0, 0.033, sym,
            ha='center', va='center',
            fontsize=7.5, color=_MOON_COLOR, fontfamily='DejaVu Sans',
        )
        fig.text(
            x0 + 0.012, 0.033, desc,
            ha='left', va='center',
            fontsize=6.5, color=_MOON_COLOR,
        )
    fig.text(
        0.91, 0.014,
        f'{name_zh}潮位站　Tidal Observation Data Annual Report {year}',
        ha='right', va='center',
        fontsize=4.5, color='#6c757d',
    )

    # ── 輸出 PNG ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format='png',
        dpi=150,
        facecolor='white',
        edgecolor='none',
    )
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ──────────────────────────────────────────────────────────────────────────────
# HTML 包裝（A4 頁面容器，供 WeasyPrint 轉 PDF 用）
# ──────────────────────────────────────────────────────────────────────────────
def render_html(png_bytes: bytes, stid: str, year: int, station: dict) -> str:
    """將 PNG bytes 包裝成單頁 HTML，嵌入 base64 inline image。"""
    b64      = base64.b64encode(png_bytes).decode('ascii')
    name_zh  = station.get('name_zh', stid)
    name_en  = station.get('name_en', stid)
    title    = f'{name_zh}潮位站 {year} 潮位觀測時序圖'

    # 使用 f-string；CSS 大括號以 {{ }} 跳脫
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  @page {{ size: A4 portrait; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 210mm; height: 297mm;
    background: #fff; overflow: hidden;
  }}
  .page {{
    width: 210mm; height: 297mm;
    display: flex; align-items: center; justify-content: center;
  }}
  img {{ width: 210mm; height: 297mm; display: block; }}
</style>
</head>
<body>
<div class="page">
  <img src="data:image/png;base64,{b64}" alt="{title}">
</div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
# 公開 API（供 generate_report.py pipeline 呼叫）
# ──────────────────────────────────────────────────────────────────────────────
def generate(
    conn,
    stid: str,
    year: int,
    station_info: dict | None = None,
) -> str:
    """
    查詢 DB → 繪圖 → 回傳完整 HTML 字串。

    Parameters
    ----------
    conn          : PyMySQL 連線（DictCursor）
    stid          : 測站舊站碼，例 '1516'
    year          : 西元年
    station_info  : {'name_zh', 'name_en', 'lat', 'lon'}；
                    None 時從模組常數 STATION_INFO 取，再找不到則用 stid 代替站名。
    """
    station = station_info or STATION_INFO.get(stid, {
        'name_zh': stid, 'name_en': stid, 'lat': '', 'lon': '',
    })

    print(f'[report4] 查詢 tide6：STID={stid}，YEAR={year}')
    data  = query_tide6(conn, stid, year)
    total = sum(len(m) for m in data.values())
    print(f'[report4] 取得 {total} 筆逐時水位資料')

    if total == 0:
        raise ValueError(f'[report4] 查無資料：STID={stid}，YEAR={year}')

    print('[report4] 繪製 12 個月時序圖…')
    png_bytes = create_sequence_figure(data, stid, year, station)
    print(f'[report4] 圖形完成（{len(png_bytes) // 1024} KB PNG）')

    return render_html(png_bytes, stid, year, station)


# ──────────────────────────────────────────────────────────────────────────────
# CLI 進入點
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    _ensure_fonts()

    parser = argparse.ArgumentParser(description='潮位觀測時序圖（Report 4）產生器')
    parser.add_argument('--stid',      required=True,           help='測站代碼，例：1516')
    parser.add_argument('--year',      required=True, type=int, help='西元年，例：2025')
    parser.add_argument('--html-only', action='store_true',
                        help='只輸出 HTML，略過 PDF（快速預覽）')
    parser.add_argument('--debug-env', action='store_true',
                        help='印出 .env 讀取詳情')
    args = parser.parse_args()

    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        html = generate(conn, args.stid, args.year)

        out_dir = Path(__file__).parent / 'output'
        out_dir.mkdir(exist_ok=True)
        stem = f'report4_{args.stid}_{args.year}'

        html_path = out_dir / f'{stem}.html'
        html_path.write_text(html, encoding='utf-8')
        print(f'[report4] HTML → {html_path.resolve()}')

        if args.html_only:
            print('[report4] ✓ 完成（HTML only）')
            return

        try:
            from weasyprint import HTML as WP
        except ImportError:
            print('⚠ WeasyPrint 未安裝。請執行：pip install WeasyPrint')
            print('  或改用 --html-only 先確認圖形。')
            sys.exit(1)

        pdf_path = out_dir / f'{stem}.pdf'
        print('[report4] 轉換 PDF 中（首次可能需要 10–30 秒）…')
        WP(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(pdf_path))
        print(f'[report4] PDF  → {pdf_path.resolve()}')
        print('[report4] ✓ 完成')

    finally:
        conn.close()


if __name__ == '__main__':
    main()
