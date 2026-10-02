"""
generate_report.py — 潮汐觀測年報主 pipeline

用法：
    python generate_report.py --config config.yaml
    python generate_report.py --config config.yaml --stid 1516       ← 只處理單站
    python generate_report.py --config config.yaml --html-only       ← 略過 PDF
    python generate_report.py --config config.yaml --debug-env

輸出：
    output/pdf_parts/         中間 PDF 暫存
    output/{filename}.pdf     最終合併 PDF

依賴套件：
    pip install pymysql jinja2 lunardate astral matplotlib numpy
    pip install PyYAML pypdf WeasyPrint markdown
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ──────────────────────────────────────────────────────────────────────────────
# 選用套件
# ──────────────────────────────────────────────────────────────────────────────
try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

try:
    from weasyprint import HTML as _WP
    _WP_OK = True
except ImportError:
    _WP_OK = False

try:
    from pypdf import PdfWriter, PdfReader
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

try:
    import markdown as _md
    _MD_OK = True
except ImportError:
    _MD_OK = False

# Report 模組
from db import load_env, get_connection
from station_list import ELIGIBLE_STATION_SQL
from report1_hourly_record   import generate as _gen_r1
from report2_daily_hl        import generate as _gen_r2
from report3_annual_stats    import generate as _gen_r3
from report4_tidal_sequences import generate as _gen_r4
from colophon_page           import html_colophon as _html_colophon
from explanation_page        import html_explanation as _html_explanation_md
from station_map             import render_station_map


# ──────────────────────────────────────────────────────────────────────────────
# 共用樣式片段（嵌入各輔助 HTML 頁面）
# ──────────────────────────────────────────────────────────────────────────────
_COMMON_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;700
  &family=Noto+Sans+TC:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500
  &display=swap" rel="stylesheet">
<style>
  :root { --ink:#1D3557; --steel:#457B9D; --hairline:#C9D2D8; --text:#212529; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Noto Sans TC',sans-serif; color:var(--text); }
  @page  { size:A4 portrait; margin:0; }
  .page  {
    width:210mm; min-height:297mm; padding:20mm 18mm 18mm;
    display:flex; flex-direction:column;
  }
</style>
"""


# ──────────────────────────────────────────────────────────────────────────────
# 輔助 HTML 頁面
# ──────────────────────────────────────────────────────────────────────────────
def _html_cover(year: int, roc_year: int, bg_image_b64: str | None = None) -> str:
    """封面頁：純文字版（如需圖片封面，請替換 bg_image_b64）。"""
    bg_css = (
        f"background:url('data:image/png;base64,{bg_image_b64}') center/cover no-repeat;"
        if bg_image_b64 else 'background:#f8f9fa;'
    )
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>潮汐觀測資料年報 {year}</title>{_COMMON_STYLE}
<style>
  .page {{ {bg_css} justify-content:center; align-items:center; text-align:center; gap:6mm; }}
  .logo {{ font-family:'Noto Serif TC',serif; font-size:14px; color:var(--steel);
           border-bottom:1px solid var(--hairline); padding-bottom:4mm; margin-bottom:4mm; width:60mm; }}
  .title-zh  {{ font-family:'Noto Serif TC',serif; font-size:28px; font-weight:700;
                color:var(--ink); letter-spacing:4px; line-height:1.4; }}
  .title-en  {{ font-family:'Noto Sans TC',sans-serif; font-size:14px; color:var(--steel);
                letter-spacing:2px; margin-top:3mm; }}
  .year-zh   {{ font-size:16px; color:var(--ink); margin-top:8mm; font-weight:600; }}
  .publisher {{ font-size:10px; color:#6c757d; margin-top:auto; }}
</style>
</head><body>
<div class="page">
  <div class="logo">中央氣象署　Central Weather Administration</div>
  <div class="title-zh">潮汐觀測資料年報</div>
  <div class="title-en">Tidal Observation Data Annual Report</div>
  <div class="year-zh">中華民國 {roc_year} 年（{year}）</div>
  <div class="publisher">交通部中央氣象署　海象氣候組</div>
</div>
</body></html>"""


def _html_separator(title_zh: str, title_en: str) -> str:
    """章節分隔頁：大型中英標題置中。"""
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>{title_zh}</title>{_COMMON_STYLE}
<style>
  .page {{ justify-content:center; align-items:center; text-align:center; }}
  .sep-zh {{ font-family:'Noto Serif TC',serif; font-size:32px; font-weight:700;
             color:var(--ink); letter-spacing:4px; }}
  .sep-en {{ font-size:16px; color:var(--steel); letter-spacing:3px; margin-top:5mm; }}
  .sep-rule {{ width:40mm; height:1px; background:var(--hairline); margin:8mm auto; }}
</style>
</head><body>
<div class="page">
  <div class="sep-rule"></div>
  <div class="sep-zh">{title_zh}</div>
  <div class="sep-en">{title_en}</div>
  <div class="sep-rule"></div>
</div>
</body></html>"""


def _html_toc(entries: list[dict], year: int, roc_year: int) -> str:
    """
    目錄頁。entries = [{'title_zh', 'title_en', 'page_num', 'indent'}]
    indent=True 表示縮排（子條目）。
    """
    # 列距依條目數自動調整（約 29 條以內 2mm；條目多時縮小，盡量維持單頁）
    pad_mm = max(0.6, min(2.0, (215.0 / max(len(entries), 1) - 3.7) / 2.0))
    rows_html = ''
    for e in entries:
        cls  = ' class="toc-sub"' if e.get('indent') else ''
        rows_html += (
            f'<tr{cls}>'
            f'<td class="toc-zh">{e["title_zh"]}</td>'
            f'<td class="toc-en">{e.get("title_en","")}</td>'
            f'<td class="toc-pg">{e["page_num"]}</td>'
            f'</tr>\n'
        )
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>目錄 {year}</title>{_COMMON_STYLE}
<style>
  .toc-title {{ font-family:'Noto Serif TC',serif; font-size:18px; font-weight:700;
                color:var(--ink); text-align:center; margin-bottom:8mm; }}
  .toc-title .en {{ font-size:10px; color:var(--steel); display:block; margin-top:2mm; }}
  table.toc {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
  .toc td {{
    padding:{pad_mm:.2f}mm 0; border-bottom:0.5px solid var(--hairline);
    font-size:9px; color:var(--ink); line-height:1.5; vertical-align:baseline;
  }}
  .toc tr.toc-sub td {{ font-size:8px; color:#555; }}
  .toc tr.toc-sub td.toc-zh {{ padding-left:6mm; }}
  .toc td.toc-en {{ color:var(--steel); }}
  .toc td.toc-pg {{ text-align:right; font-family:'IBM Plex Mono',monospace; font-weight:600; }}
</style>
</head><body>
<div class="page">
  <div class="toc-title">
    目　錄
    <span class="en">Contents</span>
  </div>
  <table class="toc">
    <colgroup><col style="width:48%"><col style="width:42%"><col style="width:10%"></colgroup>
    {rows_html}
  </table>
  <div style="margin-top:auto;font-size:7px;color:#6c757d;text-align:right;border-top:0.5px solid var(--hairline);padding-top:2mm;">
    Tidal Observation Data Annual Report {year}
  </div>
</div>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# 靜態圖頁（基準面圖解、潮差分布圖等：標題 + 一張圖）
# ──────────────────────────────────────────────────────────────────────────────
def _html_image_page(title_zh: str, title_en: str, img_path: str | Path,
                     year: int, roc_year: int) -> str | None:
    """
    依圖片長寬比自動選 A4 直式或橫式，圖片等比縮放置中；找不到圖檔回傳 None。
    圖片尺寸以 mm 明確指定，並把頁面高度壓在 A4 以內，避免溢出成空白第二頁。
    """
    p = Path(img_path)
    if not p.exists():
        return None
    try:
        from PIL import Image
        with Image.open(p) as im:
            w_px, h_px = im.size
    except Exception:
        w_px, h_px = 4, 3                      # 讀不到尺寸 → 當作橫式
    landscape = w_px > h_px * 1.15
    if landscape:
        page_w, page_h, box_w, box_h = 297, 210, 269, 140
    else:
        page_w, page_h, box_w, box_h = 210, 297, 182, 225
    scale  = min(box_w / w_px, box_h / h_px)
    disp_w = w_px * scale
    disp_h = h_px * scale
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>{title_zh}</title>{_COMMON_STYLE}
<style>
  @page {{ size:{page_w}mm {page_h}mm; margin:0; }}
  .page {{ width:{page_w}mm; height:{page_h - 1}mm; min-height:0; overflow:hidden;
           padding:14mm 14mm 10mm; }}
  .img-title {{ text-align:center; margin-bottom:6mm; }}
  .img-title .zh {{ font-family:'Noto Serif TC',serif; font-size:18px; font-weight:700;
                    color:var(--ink); letter-spacing:2px; display:block; }}
  .img-title .en {{ font-size:13px; font-weight:600; color:var(--steel);
                    display:block; margin-top:2px; }}
  .img-fig {{ text-align:center; }}
  .img-fig img {{ width:{disp_w:.1f}mm; height:{disp_h:.1f}mm; }}
  .img-foot {{ margin-top:auto; padding-top:3mm; border-top:1px solid var(--hairline);
               display:flex; justify-content:space-between; font-size:8px; color:#6c757d;
               font-family:'IBM Plex Mono',monospace; }}
</style>
</head><body>
<div class="page">
  <div class="img-title">
    <span class="zh">{title_zh}</span>
    <span class="en">{title_en}</span>
  </div>
  <div class="img-fig"><img src="{p.resolve().as_uri()}" alt="{title_zh}"></div>
  <div class="img-foot">
    <div></div>
    <div>Tidal Observation Data Annual Report {year}</div>
  </div>
</div>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# 說明文字頁（content/explanation.md → HTML）
# ──────────────────────────────────────────────────────────────────────────────
def _html_explanation(md_path: str | Path, year: int, roc_year: int) -> str | None:
    return _html_explanation_md(md_path, year, roc_year, _COMMON_STYLE)
    # ↓ 舊版簡易轉換，已不會執行，確認新版正常後可整段刪除
    p = Path(md_path)
    if not p.exists():
        print(f"[警告] 找不到說明檔，略過產生說明頁：{p.resolve()}") # 加入這行幫助偵錯
        return None
    text = p.read_text(encoding='utf-8')
    if _MD_OK:
        body = _md.markdown(text, extensions=['nl2br'])
    else:
        body = f'<pre style="white-space:pre-wrap;font-size:8px">{text}</pre>'
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>說明 {year}</title>{_COMMON_STYLE}
<style>
  .page {{ font-size:8.5px; line-height:1.8; }}
  h1,h2,h3 {{ color:var(--ink); }}
  p {{ margin:0 0 2mm; }}
</style>
</head><body>
<div class="page">{body}</div></body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# Config / DB 輔助
# ──────────────────────────────────────────────────────────────────────────────
def load_config(path: str | Path) -> dict:
    if not _YAML_OK:
        raise RuntimeError('PyYAML 未安裝，請執行：pip install PyYAML')
    with open(path, encoding='utf-8') as f:
        return _yaml.safe_load(f)


_SQL_STATION = """
SELECT s.stnac, s.stnae, s.latit, s.longit,
       s.location, s.sponsor, s.sponsore,
       kp.TGBM
FROM   st s
LEFT JOIN (
    SELECT STID, TGBM FROM tiderlkp WHERE END IS NULL
) kp ON s.stid = kp.STID
WHERE  s.stid = %s AND s.reliable = '1'
  AND  s.kind IN ('6', '9') AND s.sponsor = '中央氣象署'
ORDER  BY CASE s.kind WHEN '6' THEN 0 ELSE 1 END
LIMIT  1
"""

_SQL_STATION_LIST = """
SELECT s.stid FROM st s
WHERE """ + ELIGIBLE_STATION_SQL + """
ORDER BY CAST(s.stid AS UNSIGNED)
"""

def _strip_station_suffix(name: str) -> str:
    """st.stnac 已含「潮位站」→ 去掉，避免模板再接一次造成重複。"""
    return name.removesuffix('潮位站')

def get_station_info(conn, stid: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(_SQL_STATION, (stid,))
        r = cur.fetchone()
    if r is None:
        return {'name_zh': stid, 'name_en': stid,
                'lat': '', 'lon': '', 'location': '', 'benchmark_id': '', 'sponsor': ''}
    return {
        'name_zh':      _strip_station_suffix(str(r.get('stnac') or stid)),
        'name_en':      str(r.get('stnae') or stid),
        'lat':          str(r.get('latit')  or ''),
        'lon':          str(r.get('longit') or ''),
        'location':     str(r.get('location') or ''),
        'benchmark_id': str(r.get('TGBM')   or ''),
        'sponsor':      str(r.get('sponsor') or ''),
    }


def get_station_list(conn, cfg: dict) -> list[str]:
    year = int(cfg.get('year', 2026))
    with conn.cursor() as cur:
        cur.execute(_SQL_STATION_LIST, {'year': year})
        eligible = [str(r['stid']) for r in cur.fetchall()]

    st_cfg = cfg.get('stations', {})
    if st_cfg.get('auto', False):
        return eligible

    wanted  = [str(s) for s in st_cfg.get('list', [])]
    ok      = set(eligible)
    dropped = [s for s in wanted if s not in ok]
    if dropped:
        print(f'[pipeline] ⚠ 下列站碼不符收錄條件（kind 6/9、reliable=1、sponsor=中央氣象署、'
              f'{year} 年 tidestat 有資料）或不在 st 表，已略過：{dropped}')
    return [s for s in wanted if s in ok]

# ──────────────────────────────────────────────────────────────────────────────
# PDF 轉換與頁數統計
# ──────────────────────────────────────────────────────────────────────────────
def html_to_pdf(html: str, pdf_path: Path, base_url: str | None = None) -> int:
    """
    WeasyPrint 轉 PDF，回傳頁數。
    html-only 模式：直接存 HTML，回傳估算頁數。
    """
    if not _WP_OK:
        raise RuntimeError('WeasyPrint 未安裝')
    burl = base_url or str(Path(__file__).parent)
    pdf_bytes = _WP(string=html, base_url=burl).write_pdf()
    pdf_path.write_bytes(pdf_bytes)
    if not _PYPDF_OK:
        return 1  # fallback 估計
    return len(PdfReader(str(pdf_path)).pages)


def merge_pdfs(pdf_paths: list[Path], output: Path) -> None:
    if not _PYPDF_OK:
        raise RuntimeError('pypdf 未安裝，請執行：pip install pypdf')
    writer = PdfWriter()
    for p in pdf_paths:
        writer.append(str(p))
    with open(output, 'wb') as f:
        writer.write(f)
    writer.close()


# ── 頁碼（合併後用 pypdf 疊在每頁底部中央；不改各段版面，頁數不變）──────────
_PN_FONT_PT     = 8.0                       # 頁碼字級（pt）
_PN_BASELINE_MM = 3.6                       # 數字基線距紙張底邊（mm）
_PN_COLOR       = (0.114, 0.208, 0.341)     # #1D3557
_PN_NO_KEYS     = ('cover', 'colophon', 'back_cover')   # 另外 sep_ 開頭者也不印
_MM             = 72.0 / 25.4


def _pn_skip(sec: dict) -> bool:
    return sec['key'] in _PN_NO_KEYS or sec['key'].startswith('sep_')


def _pn_stamp_page(writer, page, text: str, font_ref) -> None:
    """
    在該頁底部中央疊印頁碼。依 CropBox 與 /Rotate 換算位置；Helvetica 內建字型，免內嵌。
    作法是在 /Contents 前後各加一段新的 content stream（q … Q 包住原內容），
    不改動原內容串，因此即使來源 PDF 的多頁共用同一內容串也不會互相影響。
    """
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
    box = page.cropbox
    x0, y0, x1, y1 = float(box.left), float(box.bottom), float(box.right), float(box.top)
    rot = int(page.rotation or 0) % 360
    w_disp = (y1 - y0) if rot in (90, 270) else (x1 - x0)
    u = w_disp / 2.0 - 0.556 * _PN_FONT_PT * len(text) / 2.0    # 數字字寬皆 0.556 em
    v = _PN_BASELINE_MM * _MM
    if rot == 0:
        m, ex, ey = (1, 0, 0, 1),   x0 + u, y0 + v
    elif rot == 90:
        m, ex, ey = (0, 1, -1, 0),  x1 - v, y0 + u
    elif rot == 180:
        m, ex, ey = (-1, 0, 0, -1), x1 - u, y1 - v
    else:
        m, ex, ey = (0, -1, 1, 0),  x0 + v, y1 - u
    r, g, b = _PN_COLOR
    stamp = (f'Q\nq BT /FPN {_PN_FONT_PT} Tf {r} {g} {b} rg '
             f'{m[0]} {m[1]} {m[2]} {m[3]} {ex:.2f} {ey:.2f} Tm ({text}) Tj ET Q\n')

    head, tail = DecodedStreamObject(), DecodedStreamObject()
    head.set_data(b'q\n')
    tail.set_data(stamp.encode('ascii'))
    raw = dict.get(page, '/Contents')                    # 不解參照，保留原本的間接物件
    old = raw.get_object() if raw is not None else None
    if old is None:
        old_items = []
    elif isinstance(old, ArrayObject):
        old_items = list(old)
    else:
        old_items = [raw]
    page[NameObject('/Contents')] = ArrayObject(
        [writer._add_object(head)] + old_items + [writer._add_object(tail)])

    res = page.get('/Resources')
    res = res.get_object() if res is not None else None
    if res is None:
        res = DictionaryObject()
        page[NameObject('/Resources')] = res
    fonts = res.get('/Font')
    fonts = fonts.get_object() if fonts is not None else None
    if fonts is None:
        fonts = DictionaryObject()
        res[NameObject('/Font')] = fonts
    fonts[NameObject('/FPN')] = font_ref


def _pn_ink_pages(pdf_path: Path, numbered: list[int]) -> list[int]:
    """疊印前檢查：頁碼位置（底部中央一小格）已有內容的頁（1 起算）。需要 pypdfium2。"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print('[頁碼] pypdfium2 未安裝，略過「頁碼位置是否已有內容」檢查')
        return []
    hit: list[int] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        y_lo = (_PN_BASELINE_MM - 1.0) * _MM
        y_hi = _PN_BASELINE_MM * _MM + 0.72 * _PN_FONT_PT + 0.3 * _MM
        half = 6.0 * _MM
        for n in numbered:
            page = pdf[n - 1]
            w, h = page.get_size()
            bmp = page.render(scale=2, grayscale=True,
                              crop=(w / 2 - half, y_lo, w / 2 - half, h - y_hi))
            if (bmp.to_numpy() < 200).any():
                hit.append(n)
            page.close()
    finally:
        pdf.close()
    return hit


def stamp_page_numbers(src: Path, dst: Path, secs: list[dict]) -> None:
    """
    src = 已合併、尚未編號的 PDF；secs = 與合併順序相同的段落清單（含 page_count、key）。
    封面、版權頁、封底、分隔頁照算頁數但不印；其餘依合併順序印 1,2,3…
    """
    if not _PYPDF_OK:
        raise RuntimeError('pypdf 未安裝，請執行：pip install pypdf')
    plan: list[tuple[int, bool, str]] = []        # (頁序 1 起算, 是否印, 段落 key)
    n = 0
    for s in secs:
        for _ in range(s['page_count']):
            n += 1
            plan.append((n, not _pn_skip(s), s['key']))

    reader = PdfReader(str(src))
    if len(reader.pages) != n:
        raise RuntimeError(f'[頁碼] 合併後共 {len(reader.pages)} 頁，但各段頁數加總為 {n}，頁碼會對不上，已中止')

    blocked = _pn_ink_pages(src, [p for p, ok, _ in plan if ok])

    from pypdf.generic import DictionaryObject, NameObject
    writer = PdfWriter(clone_from=reader)
    font_ref = writer._add_object(DictionaryObject({
        NameObject('/Type'):     NameObject('/Font'),
        NameObject('/Subtype'):  NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
        NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
    }))
    for (p, ok, _), page in zip(plan, writer.pages):
        if ok:
            _pn_stamp_page(writer, page, str(p), font_ref)
    with open(dst, 'wb') as f:
        writer.write(f)
    writer.close()

    # 摘要：連續同類段落合併成一行
    print('[頁碼] 頁碼對照（封面算第 1 頁；Adobe 的頁碼框應與印出頁碼一致）：')
    groups: list[list] = []
    for p, ok, key in plan:
        kind = key.split('_')[0] if key.startswith('report') else key
        if key.startswith('sep_'):
            kind = 'sep'
        if groups and groups[-1][0] == kind and groups[-1][1] == ok and groups[-1][3] == p - 1:
            groups[-1][3] = p
        else:
            groups.append([kind, ok, p, p])
    for kind, ok, a, b in groups:
        rng = f'{a}' if a == b else f'{a}–{b}'
        print(f'[頁碼]   {rng:>10}  {kind:<24}{"印頁碼" if ok else "不印"}')
    if blocked:
        print(f'[頁碼] ⚠ 下列頁的頁碼位置（底部中央）已有內容，疊印後可能重疊，請目視檢查：{blocked}')
    else:
        print('[頁碼] ✓ 頁碼位置檢查：無重疊')


def _static_pdf_section(title_zh: str, title_en: str, key: str,
                        src: Path, no_toc: bool = False) -> dict | None:
    """
    靜態 PDF 段落：不經 WeasyPrint，原檔（向量、不失真）直接交給 pypdf 合併。
    檔案不存在或讀不到頁數 → 回傳 None，由呼叫端印警告。
    """
    if not _PYPDF_OK or not src.exists():
        return None
    try:
        n = len(PdfReader(str(src)).pages)
    except Exception:
        return None
    return {
        'title_zh': title_zh, 'title_en': title_en,
        'key': key, 'html': None, 'static_pdf': True,
        'pdf_path': src, 'page_count': n, 'start_page': 0,
        'indent': False, 'stid': None, 'label_zh': None, 'no_toc': no_toc,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 段落定義輔助
# ──────────────────────────────────────────────────────────────────────────────
def _section(title_zh: str, title_en: str,
             html: str, key: str,
             pdf_dir: Path, year: int) -> dict:
    """段落描述符：儲存 HTML，準備後續轉 PDF。"""
    return {
        'title_zh': title_zh, 'title_en': title_en,
        'key': key, 'html': html,
        'pdf_path': pdf_dir / f'{key}.pdf',
        'page_count': 0,
        'start_page': 0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────
def run(conn, cfg: dict, only_stid: str | None = None,
        html_only: bool = False) -> Path:
    """
    執行完整 pipeline，回傳最終輸出 PDF 路徑（html-only 模式回傳 html 目錄）。
    """
    year        = int(cfg.get('year', 2026))
    roc_year    = year - 1911
    inc         = cfg.get('include', {})
    order       = cfg.get('output_order', 'by_station')
    out_cfg     = cfg.get('output', {})
    pdf_dir_str = out_cfg.get('pdf_dir', 'output')
    filename    = out_cfg.get('filename', f'tide_report_{year}.pdf').replace('{year}', str(year))

    root     = Path(__file__).parent
    pdf_dir  = root / pdf_dir_str
    parts_dir = pdf_dir / 'pdf_parts'
    html_dir  = pdf_dir / 'html'

    parts_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    station_list = get_station_list(conn, cfg)
    full_station_list = list(station_list)          # 位置圖用全站，單站測試時也畫全部
    if only_stid:
        station_list = [s for s in station_list if s == only_stid]
    if not station_list:
        raise ValueError(f'站碼清單為空，請確認 config.yaml 或 --stid 參數')

    print(f'[pipeline] YEAR={year}，測站 {len(station_list)} 座，輸出順序={order}')

    # ── Step 1：產生各段落 HTML ──────────────────────────────────────────
    sections: list[dict] = []

    # 封面
    cov_path = inc.get('cover', True) and cfg.get('cover', {}).get('bg_image')
    cov_b64 = None
    if cov_path:
        try:
            import base64
            cov_b64 = base64.b64encode(
                (root / cov_path).read_bytes()
            ).decode('ascii')
        except Exception:
            pass
    if inc.get('cover', True):
        sections.append(_section(
            '封面', 'Cover',
            _html_cover(year, roc_year, cov_b64),
            'cover', parts_dir, year,
        ))

    # 說明文字
    if inc.get('explanation', True):
        md_src = cfg.get('content', {}).get('explanation_md', 'content/explanation.md')
        expl   = _html_explanation(root / md_src, year, roc_year)
        if expl:
            sections.append(_section('說明', 'Explanation', expl, 'explanation', parts_dir, year))

    # 測站一覽表
    if inc.get('station_table', True):
        try:
            from station_list import generate as _gen_st
            st_html = _gen_st(conn, year, station_list)
            sections.append(_section('潮位站一覽表', 'Table of Tide Stations',
                                     st_html, 'station_table', parts_dir, year))
        except Exception as e:
            print(f'[pipeline] ⚠ 潮位站一覽表失敗（{e}），跳過')

    # # 測站位置圖：matplotlib 疊在底圖上 → PNG → 圖頁
    # if inc.get('station_map', True):
    #     try:
    #         map_png = render_station_map(conn, full_station_list, cfg, root,
    #                                      html_dir / 'station_map.png')
    #         map_html = (_html_image_page('潮位站位置圖', 'Map of Tide Stations',
    #                                      map_png, year, roc_year) if map_png else None)
    #         if map_html:
    #             sections.append(_section('潮位站位置圖', 'Map of Tide Stations',
    #                                      map_html, 'station_map', parts_dir, year))
    #     except Exception as e:
    #         print(f'[pipeline] ⚠ 測站位置圖失敗（{e}），跳過')

    # 靜態圖頁：基準面圖解、潮差分布圖（圖檔路徑見 config.yaml 的 assets）
    assets_cfg = cfg.get('assets', {})
    for inc_key, asset_key, t_zh, t_en in [
        # ('Map of Tide Stations', 'Map of Tide Stations', '測站位置圖',     'taiwan_outline_original.png'),
        ('taiwan_outline_original', 'taiwan_outline_original', '測站位置圖', 'Map of Tide Stations'),
        ('reference_level', 'reference_level', '基準面圖解',     'Reference Level Diagram'),
        ('tidal_range_map', 'tidal_range',     '台灣潮差分布圖', 'Tidal Range Map of Taiwan'),
    ]:
        if not inc.get(inc_key, True):
            continue
        img_rel  = assets_cfg.get(asset_key)
        if img_rel and Path(img_rel).suffix.lower() == '.pdf':     # .pdf → 原檔直接合併（向量、不失真）
            sec = _static_pdf_section(t_zh, t_en, inc_key, root / img_rel)
            if sec is None:
                print(f'[pipeline] ⚠ {t_zh}：找不到或無法讀取 PDF（assets.{asset_key} = {img_rel}），跳過')
            else:
                sections.append(sec)
            continue
        img_html = _html_image_page(t_zh, t_en, root / img_rel, year, roc_year) if img_rel else None
        if img_html is None:
            print(f'[pipeline] ⚠ {t_zh}：找不到圖檔（assets.{asset_key} = {img_rel}），跳過')
            continue
        sections.append(_section(t_zh, t_en, img_html, inc_key, parts_dir, year))

    # ── 各測站報表 ────────────────────────────────────────────────────
    r3_sections: list[dict] = []
    r2_sections: list[dict] = []
    r1_sections: list[dict] = []
    r4_sections: list[dict] = []

    for stid in station_list:
        si = get_station_info(conn, stid)
        name_zh = si['name_zh']
        print(f'[pipeline] 處理測站：{stid}（{name_zh}）')

        if order == 'by_station':
            if inc.get('station_separator', True):
                sep_html = _html_separator(
                    f'{name_zh}潮位站', f'{si["name_en"]} Tide Station')
                sections.append(_section(
                    f'{name_zh}潮位站', f'{si["name_en"]} Tide Station',
                    sep_html, f'sep_{stid}', parts_dir, year,
                ))

        for report_key, gen_fn, label_zh, label_en, store in [
            ('report3', _gen_r3, '逐月統計年報', 'Annual Statistics', r3_sections),
            ('report2', _gen_r2, '逐日高低潮月報', 'Daily H/L Summary', r2_sections),
            ('report1', _gen_r1, '逐時潮位紀錄表', 'Hourly Tidal Record', r1_sections),
            ('report4', _gen_r4, '潮位時序圖', 'Tidal Sequences', r4_sections),
        ]:
            if not inc.get(report_key, True):
                continue
            try:
                html = gen_fn(conn, stid, year, si)
                key  = f'{report_key}_{stid}'
                (html_dir / f'{key}.html').write_text(html, encoding='utf-8')
                sec = _section(
                    f'{name_zh}—{label_zh}',
                    f'{si["name_en"]}—{label_en}',
                    html, key, parts_dir, year,
                    indent=True,
                )
                sec['stid']     = stid
                sec['label_zh'] = label_zh
                store.append(sec)
                if order == 'by_station':
                    sections.append(sec)
            except Exception as e:
                print(f'[pipeline] ⚠ {stid} {label_zh} 失敗（{e}），跳過')

    # by_type：章節 separator + 各型報表
    if order == 'by_type':
        for store, sep_zh, sep_en in [
            (r3_sections, '逐月統計年報表', 'Annual Statistics Tables'),
            (r2_sections, '逐日高低潮月報表', 'Daily H/L Summary Tables'),
            (r1_sections, '逐時潮位紀錄表', 'Hourly Tidal Record Tables'),
            (r4_sections, '潮位觀測時序圖', 'Tidal Observation Sequences'),
        ]:
            if store:
                if inc.get('station_separator', True):
                    sections.append(_section(
                        sep_zh, sep_en,
                        _html_separator(sep_zh, sep_en),
                        f'sep_{sep_zh}', parts_dir, year,
                    ))
                sections.extend(store)

    # 版權頁 → 封底：排在全書最後，兩者皆不列入目錄（no_toc）
    if inc.get('colophon', True):
        col_src  = cfg.get('content', {}).get('colophon_yaml', 'content/colophon.yaml')
        col_html = _html_colophon(root / col_src, year, roc_year, _COMMON_STYLE)
        if col_html:
            (html_dir / 'colophon.html').write_text(col_html, encoding='utf-8')
            col_sec = _section('版權頁', 'Colophon', col_html, 'colophon', parts_dir, year)
            col_sec['no_toc'] = True
            sections.append(col_sec)

    if inc.get('back_cover', True):
        bc_rel = assets_cfg.get('back_cover')
        # bc_sec = (_static_pdf_section('封底', 'Back Cover', 'back_cover',
        bc_sec = (_static_pdf_section('back_cover', 'Back Cover', 'back_cover',
                                      root / bc_rel, no_toc=True) if bc_rel else None)
        if bc_sec is None:
            print(f'[pipeline] ⚠ 封底：找不到或無法讀取 PDF（assets.back_cover = {bc_rel}），跳過')
        else:
            sections.append(bc_sec)

    if html_only:
        print(f'[pipeline] ✓ HTML 已存至 {html_dir.resolve()}')
        return html_dir

    # ── Step 2：轉換各段落為 PDF，統計頁數 ─────────────────────────────
    if not _WP_OK:
        raise RuntimeError('WeasyPrint 未安裝，無法產生 PDF。請執行：pip install WeasyPrint')

    print('[pipeline] 轉換 PDF 中（可能需要數分鐘）…')
    for sec in sections:
        print(f'[pipeline]   {sec["title_zh"]}…', end=' ', flush=True)
        if sec.get('static_pdf'):                     # 靜態 PDF：頁數已在建立時讀好，不需轉檔
            print(f'{sec["page_count"]} 頁（原檔直接合併）')
            continue
        try:
            n = html_to_pdf(sec['html'], sec['pdf_path'])
            sec['page_count'] = n
            print(f'{n} 頁')
        except Exception as e:
            print(f'失敗（{e}），跳過')
            sec['page_count'] = 0

    # ── Step 3：計算頁碼（兩次轉換，確保目錄頁碼正確）─────────────────
    if inc.get('toc', True):
        # 目錄層級：station = 只列前頁與各站（by_type 則各類型）；full = 每份報表都列
        toc_level = str(cfg.get('toc_level', 'station')).lower()
        if toc_level not in ('station', 'full'):
            print(f'[pipeline] ⚠ toc_level={toc_level!r} 無法辨識，改用 station')
            toc_level = 'station'
        if toc_level == 'station' and not inc.get('station_separator', True):
            print('[pipeline] ⚠ station_separator 已關閉，站名層級目錄缺少對應頁，改用 full')
            toc_level = 'full'

        # 封面頁數（未啟用封面則為 0）；目錄第一次先假設 1 頁
        cover_pages = next((s['page_count'] for s in sections if s['key'] == 'cover'), 0)
        _build_page_nums(sections, content_offset=cover_pages + 2)
        toc_entries = _build_toc_entries(sections, toc_level)
        toc_html    = _html_toc(toc_entries, year, roc_year)
        toc_pdf     = parts_dir / 'toc.pdf'
        toc_pages   = html_to_pdf(toc_html, toc_pdf)

        # 內容起始頁 = 封面頁數 + 目錄實際頁數 + 1
        real_offset = cover_pages + toc_pages + 1
        if real_offset != cover_pages + 2:
            _build_page_nums(sections, content_offset=real_offset)
            toc_entries = _build_toc_entries(sections, toc_level)
            toc_html    = _html_toc(toc_entries, year, roc_year)
            html_to_pdf(toc_html, toc_pdf)

        # 在封面後插入目錄
        _insert_after_key(sections, 'cover', {
            'title_zh': '目錄', 'title_en': 'Contents',
            'key': 'toc', 'pdf_path': toc_pdf,
            'page_count': toc_pages, 'start_page': 2,
        })

    # ── Step 4：合併 PDF ────────────────────────────────────────────────
    valid_secs = [s for s in sections
                  if s.get('page_count', 0) > 0 and s['pdf_path'].exists()]
    valid_pdfs = [s['pdf_path'] for s in valid_secs]
    if not valid_pdfs:
        raise RuntimeError('[pipeline] 沒有任何有效的 PDF 段落可以合併')

    out_path = pdf_dir / filename
    print(f'[pipeline] 合併 {len(valid_pdfs)} 個 PDF 段落…')
    if inc.get('page_numbers', True):
        nonum_path = parts_dir / '_merged_nonum.pdf'     # 先合併成未編號版，再疊頁碼
        merge_pdfs(valid_pdfs, nonum_path)
        stamp_page_numbers(nonum_path, out_path, valid_secs)
    else:
        merge_pdfs(valid_pdfs, out_path)
    total = sum(s.get('page_count', 0) for s in sections)
    print(f'[pipeline] ✓ 完成！共 {total} 頁 → {out_path.resolve()}')
    return out_path


# ──────────────────────────────────────────────────────────────────────────────
# 頁碼計算輔助
# ──────────────────────────────────────────────────────────────────────────────
def _build_page_nums(sections: list[dict], content_offset: int = 3) -> None:
    """在 sections 中設定 start_page（封面/目錄為 1 起算的固定值，其他累計）。"""
    cur = content_offset
    for s in sections:
        if s['key'] in ('cover', 'toc'):
            # cover = 1, toc = 2 (固定)
            s['start_page'] = 1 if s['key'] == 'cover' else 2
        else:
            s['start_page'] = cur
            cur += s.get('page_count', 0)


def _build_toc_entries(sections: list[dict], level: str = 'full') -> list[dict]:
    """
    轉換 sections → TOC entries（不列封面與目錄本身）。
    level='full'   ：分隔頁與每份報表都列（報表縮排）。
    level='station'：只列不縮排的條目 = 前頁（說明、一覽表、圖頁…）與各站/各類型分隔頁，
                     略過每站底下的個別報表（by_station 時目錄由 5 條/站縮為 1 條/站）。
    """
    entries = [{'title_zh': '目錄', 'title_en': 'Contents', 'page_num': 2, 'indent': False}]
    for s in sections:
        if s['key'] in ('cover', 'toc') or s.get('no_toc') or s.get('page_count', 0) <= 0:
            continue
        is_sep = s['key'].startswith('sep_')
        indent = False if is_sep else s.get('indent', False)   # 分隔頁不縮排
        if level == 'station' and indent:
            continue
        entries.append({
            'title_zh': s['title_zh'],
            'title_en': s.get('title_en', ''),
            'page_num': s['start_page'],
            'indent':   indent,
        })
    return entries


def _insert_after_key(sections: list[dict], after_key: str, item: dict) -> None:
    for i, s in enumerate(sections):
        if s['key'] == after_key:
            sections.insert(i + 1, item)
            return
    sections.insert(0, item)


def _section(title_zh, title_en, html, key, pdf_dir, year,
             indent=False, stid=None, label_zh=None) -> dict:
    return {
        'title_zh': title_zh, 'title_en': title_en,
        'key': key, 'html': html,
        'pdf_path': pdf_dir / f'{key}.pdf',
        'page_count': 0, 'start_page': 0,
        'indent': indent,
        'stid': stid, 'label_zh': label_zh,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI 進入點
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description='潮汐觀測年報主 pipeline')
    parser.add_argument('--config',    default='config.yaml', help='config.yaml 路徑')
    parser.add_argument('--stid',      default=None,          help='只處理指定測站，例：1516')
    parser.add_argument('--html-only', action='store_true',   help='只產生 HTML，略過 PDF')
    parser.add_argument('--debug-env', action='store_true',   help='印出 .env 讀取詳情')
    args = parser.parse_args()

    if not _YAML_OK:
        print('⚠ PyYAML 未安裝。請執行：pip install PyYAML'); sys.exit(1)

    if not Path(args.config).exists():
        print(f'⚠ 找不到 config 檔：{args.config}'); sys.exit(1)

    cfg = load_config(args.config)
    load_env(debug=args.debug_env)
    conn = get_connection()

    try:
        out = run(conn, cfg,
                  only_stid=args.stid,
                  html_only=args.html_only)
        print(f'[pipeline] 輸出：{out}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
