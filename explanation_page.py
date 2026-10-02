"""
explanation_page.py — 說明頁（content/explanation.md → 編號、中英配對的 HTML）

md 結構約定（與 explanation.md 現況一致）：
    ## 1            → 一條說明（自動編號：中文「一、二、…」，英文「1. 2. …」）
    ### 9-1 標題    → 該條底下的子項（自動編號「(1) (2) …」，標題開頭的「9-1」會被去掉）
    其餘文字        → 依「有無漢字」逐行判斷中文/英文，同語言且連續的行合併為一段
    <!-- ... -->    → 註解，忽略
    ![說明](路徑)   → 圖片（路徑相對於 md 檔所在目錄）
    # 一級標題、---  → 忽略
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

_HAN     = re.compile(r'[\u4e00-\u9fff]')          # 只看漢字，全形標點不算
_IMG     = re.compile(r'^!\[(.*?)\]\((.+?)\)\s*$')
_HEADING = re.compile(r'^(#{1,3})\s+(.*?)\s*$')
_SUBNUM  = re.compile(r'^\d+-\d+\s*')

_CN_DIGIT = '零一二三四五六七八九'


def _cn_num(n: int) -> str:
    """1→一、10→十、11→十一、20→二十、21→二十一（100 以上直接用阿拉伯數字）。"""
    if n < 10:
        return _CN_DIGIT[n]
    if n < 20:
        return '十' + (_CN_DIGIT[n - 10] if n > 10 else '')
    if n < 100:
        return _CN_DIGIT[n // 10] + '十' + (_CN_DIGIT[n % 10] if n % 10 else '')
    return str(n)


def _inline(text: str) -> str:
    """跳脫 HTML，並支援 **粗體**、*斜體*。"""
    t = _html.escape(text.strip())
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])', r'<i>\1</i>', t)
    return t


# ──────────────────────────────────────────────────────────────────────────────
# 解析
# ──────────────────────────────────────────────────────────────────────────────
def parse_explanation(text: str) -> list[dict]:
    """
    回傳 [{'key': '1', 'lead': [lines], 'subs': [{'title': str, 'lines': [lines]}]}, ...]
    """
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    items: list[dict] = []
    target: list[str] | None = None

    for raw in text.splitlines():
        m = _HEADING.match(raw)
        if m:
            level, title = len(m.group(1)), m.group(2)
            if level == 2:
                item = {'key': title, 'lead': [], 'subs': []}
                items.append(item)
                target = item['lead']
            elif level == 3 and items:
                sub = {'title': _SUBNUM.sub('', title), 'lines': []}
                items[-1]['subs'].append(sub)
                target = sub['lines']
            continue                                    # 一級標題與其他標題：忽略
        if re.fullmatch(r'-{3,}\s*', raw):
            continue
        if target is not None:
            target.append(raw)
    return items


def _to_blocks(lines: list[str]) -> list[dict]:
    """
    逐行分類（img / zh / en）；同類型、且中間沒有空行的連續行合併成一段。
    """
    blocks: list[dict] = []
    prev_kind, blank_seen = None, True
    for raw in lines:
        s = raw.strip()
        if not s:
            blank_seen = True
            continue
        mi = _IMG.match(s)
        if mi:
            blocks.append({'kind': 'img', 'alt': mi.group(1), 'src': mi.group(2)})
            prev_kind, blank_seen = 'img', True
            continue
        kind = 'zh' if _HAN.search(s) else 'en'
        if blocks and kind == prev_kind and not blank_seen and blocks[-1]['kind'] == kind:
            blocks[-1]['lines'].append(s)
        else:
            blocks.append({'kind': kind, 'lines': [s]})
        prev_kind, blank_seen = kind, False
    return blocks


# ──────────────────────────────────────────────────────────────────────────────
# 轉 HTML
# ──────────────────────────────────────────────────────────────────────────────
def _para_html(block: dict) -> str:
    lines = block['lines']
    if block['kind'] == 'zh':
        body = '<br>'.join(_inline(l) for l in lines)          # 中文：保留換行（● 條列）
    else:
        out = ''
        for i, l in enumerate(lines):                           # 英文：硬換行併成一句，● 條列除外
            if i:
                out += '<br>' if l.startswith(('●', '•', '-')) else ' '
            out += _inline(l)
        body = out
    return f'<p class="{block["kind"]}">{body}</p>'


def _img_html(block: dict, base_dir: Path) -> str:
    p = (base_dir / block['src']).resolve()
    if not p.exists():
        print(f'[explanation] ⚠ 找不到圖檔：{block["src"]}（相對於 {base_dir}），已略過')
        return ''
    return (f'<figure><img src="{p.as_uri()}" alt="{_html.escape(block["alt"])}">'
            f'<figcaption>{_html.escape(block["alt"])}</figcaption></figure>')


def _unit_rows(blocks: list[dict], zh_no: str, en_no: str, base_dir: Path,
               cls: str = '') -> str:
    """把一組 blocks 排成「中文列 + 英文列」；編號只掛在各語言第一段。"""
    zh = [b for b in blocks if b['kind'] == 'zh']
    en = [b for b in blocks if b['kind'] == 'en']
    imgs = [b for b in blocks if b['kind'] == 'img']
    out = ''
    if zh:
        out += (f'<div class="row zh-row {cls}"><span class="no">{zh_no}</span>'
                f'<div class="txt">{"".join(_para_html(b) for b in zh)}</div></div>')
    if en:
        out += (f'<div class="row en-row {cls}"><span class="no">{en_no}</span>'
                f'<div class="txt">{"".join(_para_html(b) for b in en)}</div></div>')
    for b in imgs:
        out += _img_html(b, base_dir)
    return out


def _explanation_body(items: list[dict], base_dir: Path) -> str:
    html = ''
    for n, item in enumerate(items, 1):
        lead   = _to_blocks(item['lead'])
        has_zh = any(b['kind'] == 'zh' for b in lead)
        has_en = any(b['kind'] == 'en' for b in lead)
        if item['subs'] == [] and not (has_zh and has_en):
            print(f'[explanation] ⚠ 第 {n} 條（## {item["key"]}）'
                  f'{"缺英文" if has_zh else "缺中文" if has_en else "沒有內容"}')
        html += '<section class="item">'
        html += _unit_rows(lead, f'{_cn_num(n)}、', f'{n}.', base_dir)

        for k, sub in enumerate(item['subs'], 1):
            sb = _to_blocks(sub['lines'])
            if not (any(b['kind'] == 'zh' for b in sb) and any(b['kind'] == 'en' for b in sb)):
                print(f'[explanation] ⚠ 第 {n} 條子項「{sub["title"]}」中英文未成對')
            html += (f'<div class="sub"><div class="sub-title">'
                     f'<span class="no">({k})</span>{_html.escape(sub["title"])}</div>'
                     f'{_unit_rows(sb, "", "", base_dir, cls="in-sub")}</div>')
        html += '</section>\n'
    return html


def html_explanation(md_path: str | Path, year: int, roc_year: int,
                     common_style: str = '') -> str | None:
    """完整說明頁 HTML；找不到 md 回傳 None。common_style 傳入 generate_report._COMMON_STYLE。"""
    p = Path(md_path)
    if not p.exists():
        return None
    items = parse_explanation(p.read_text(encoding='utf-8-sig'))
    if not items:
        print(f'[explanation] ⚠ {p.name} 內沒有任何「## 編號」條目')
        return None
    body = _explanation_body(items, p.parent)

    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>說明 {year}</title>{common_style}
<style>
  /* 多頁流動內容：頁邊距交給 @page，頁尾用 margin box（頁碼由目錄另行統計） */
  @page {{
    size: A4 portrait;
    margin: 18mm 18mm 20mm;
    @bottom-right {{ content: "Tidal Observation Data Annual Report {year}";
                    font: 8px 'IBM Plex Mono', monospace; color: #6c757d; }}
  }}
  .page {{ display:block; width:auto; min-height:0; padding:0;
           font-size:9px; line-height:1.75; }}
  .ex-title {{ text-align:center; margin-bottom:7mm; }}
  .ex-title .zh {{ font-family:'Noto Serif TC',serif; font-size:18px; font-weight:700;
                   color:var(--ink); letter-spacing:2px; display:block; }}
  .ex-title .en {{ font-size:13px; font-weight:600; color:var(--steel);
                   display:block; margin-top:2px; }}

  section.item {{ margin-bottom:4.5mm; }}
  /* 編號用絕對定位掛在左側（比 flex 更能穩定跨頁） */
  .row {{ position:relative; padding-left:9mm; break-inside:avoid; }}
  .row .no {{ position:absolute; left:0; top:0; font-weight:700; color:var(--ink); }}
  .row p {{ margin:0 0 1mm; text-align:justify; }}
  .zh-row p.zh {{ color:var(--text); }}
  .en-row {{ margin-top:0.8mm; }}
  .en-row .no {{ color:var(--steel); font-weight:600; }}
  .en-row p.en {{ color:#3d4b57; font-size:8.5px; line-height:1.6; }}

  .sub {{ margin:2.2mm 0 0 9mm; padding-left:3mm; border-left:1.2px solid var(--hairline);
          break-inside:avoid; }}
  .sub-title {{ font-weight:700; color:var(--ink); font-size:9px; margin-bottom:0.6mm; }}
  .sub-title .no {{ display:inline-block; min-width:8mm; color:var(--steel); }}
  .row.in-sub {{ padding-left:0; }}
  .row.in-sub .no {{ display:none; }}

  figure {{ margin:2mm 0 2mm 9mm; text-align:center; break-inside:avoid; }}
  figure img {{ max-width:100%; max-height:110mm; }}
  figcaption {{ font-size:8px; color:#6c757d; margin-top:1mm; }}
</style>
</head><body>
<div class="page">
  <div class="ex-title"><span class="zh">說　明</span><span class="en">Explanation</span></div>
  {body}
</div></body></html>"""
