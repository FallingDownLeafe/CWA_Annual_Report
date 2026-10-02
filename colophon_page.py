"""
colophon_page.py — 版權頁（依 p999_版權頁.pdf 版面重現）

文字內容全部來自 content/colophon.yaml，每年改 yaml 即可，不需動程式。

介面（與 explanation_page.html_explanation 同型）：
    html_colophon(yaml_path, year, roc_year, common_style) -> str | None
    找不到 yaml 或格式錯誤 → 印警告並回傳 None（呼叫端會略過此頁）

自動計算的欄位：
    標題年份 = roc_year（由 config 的 year 換算）
    刊期     = roc_year - 76（創刊民國 77 年為第 1 期；110 年 = 第 34 期）
               yaml 的 issue_no 填數字時以 yaml 為準
"""
from __future__ import annotations

import re
from datetime import date
from html import escape
from pathlib import Path

try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

FIRST_ISSUE_ROC_YEAR = 77      # 創刊民國年（第 1 期）


# ──────────────────────────────────────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────────────────────────────────────
def _e(v) -> str:
    """None / 空字串 → ''；其餘做 HTML escape。"""
    if v is None:
        return ''
    return escape(str(v).strip())


def _kv(key: str, value) -> str:
    """'地址：xxx'；值為空則回傳 ''（該行不顯示）。"""
    v = _e(value)
    return f'{key}：{v}' if v else ''


def _publish_date(raw, roc_year: int) -> str:
    """
    出版年月。yaml 填 auto → 取程式執行當下的年月（中華民國 ○ 年 ○ 月）；
    填其他文字 → 原樣印出；留空 → 不顯示此行。
    印出的民國年不晚於報告年度時（多半是忘了更新舊文字）印警告。
    """
    s = '' if raw is None else str(raw).strip()
    if not s:
        return ''
    if s.lower() == 'auto':
        today = date.today()
        s = f'中華民國 {today.year - 1911} 年 {today.month} 月'
    m = re.search(r'(\d+)\s*年', s)
    if m and int(m.group(1)) <= roc_year:
        print(f'[colophon] ⚠ 出版年月「{s}」不晚於報告年度（民國 {roc_year} 年），請確認是否忘了更新')
    return escape(s)


def _label(text: str) -> str:
    """
    欄位標籤：固定 4 字寬、字距平均分散（兩字標籤如「編者」「定價」會撐開對齊），
    後接全形冒號。text 為空 → 只留空位（用於接續行）。
    """
    if not text:
        return '<div class="lab"><span class="t"></span></div>'
    chars = ''.join(f'<span>{escape(c)}</span>'
                    for c in text if not c.isspace())
    return f'<div class="lab"><span class="t">{chars}</span><span>：</span></div>'


def _row(label: str, lines: list[str]) -> str:
    """一列：標籤 + 多行內容（第 1 行貼齊冒號後，其餘行縮排）。全空則不輸出。"""
    lines = [l for l in lines if l]
    if not lines:
        return ''
    first, *rest = lines
    body = f'<div>{first}</div>' + ''.join(f'<div class="sub">{l}</div>' for l in rest)
    return f'<div class="row">{_label(label)}<div class="val">{body}</div></div>'


def _retailers_row(items: list) -> str:
    """展售處：每家含名稱、地址、電話；家與家之間空一行。"""
    blocks = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = _e(it.get('name'))
        if not name:
            continue
        blocks.append(
            '<div class="blk">'
            f'<div>{name}</div>'
            + (f'<div class="sub">{_kv("地址", it.get("address"))}</div>' if it.get('address') else '')
            + (f'<div class="sub">{_kv("電話", it.get("phone"))}</div>' if it.get('phone') else '')
            + '</div>'
        )
    if not blocks:
        return ''
    return f'<div class="row">{_label("展售處")}<div class="val">{"".join(blocks)}</div></div>'


# ──────────────────────────────────────────────────────────────────────────────
# 主函式
# ──────────────────────────────────────────────────────────────────────────────
def html_colophon(yaml_path: str | Path, year: int, roc_year: int,
                  common_style: str) -> str | None:
    p = Path(yaml_path)
    if not _YAML_OK:
        print('[colophon] ⚠ PyYAML 未安裝，略過版權頁')
        return None
    if not p.exists():
        print(f'[colophon] ⚠ 找不到版權頁設定檔，略過：{p.resolve()}')
        return None
    try:
        d = _yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        if not isinstance(d, dict):
            raise ValueError('最上層必須是 key: value 結構')
    except Exception as e:
        print(f'[colophon] ⚠ {p.name} 讀取失敗（{e}），略過版權頁')
        return None

    pub = d.get('publisher') or {}
    phones = pub.get('phones') or []
    if isinstance(phones, str):
        phones = [phones]

    issue_no = d.get('issue_no')
    if issue_no in (None, ''):
        issue_no = roc_year - (FIRST_ISSUE_ROC_YEAR - 1)
    freq = _e(d.get('frequency'))
    freq_line = f'{freq} 第 {_e(issue_no)} 期' if freq else ''

    rows = ''.join([
        _row('出版機關', [
            _e(pub.get('name')),
            _kv('地址', pub.get('address')),
            _kv('網址', pub.get('website')),
            _kv('電話', '、'.join(str(x).strip() for x in phones if str(x).strip())),
        ]),
        _row('編者',     [_e(d.get('editor'))]),
        _row('出版年月', [_publish_date(d.get('publish_date'), roc_year)]),
        _row('創刊年月', [_e(d.get('founded'))]),
        _row('刊期頻率', [freq_line]),
    ])

    note = _e(d.get('other_edition_note'))
    note_html = f'<div class="note">其他類型版本說明{note}</div>' if note else ''

    tail = ''.join([
        _row('定價', [_e(d.get('price'))]),
        _retailers_row(d.get('retailers')),
    ])

    ids = []
    if d.get('gpn'):
        ids.append(f'<span>GPN : {_e(d.get("gpn"))}</span>')
    if d.get('issn'):
        ids.append(f'<span>ISSN : {_e(d.get("issn"))}</span>')
    ids_html = f'<div class="ids">{"".join(ids)}</div>' if ids else ''

    holder = _e(d.get('copyright_holder'))
    holder_html = f'<div class="holder">著作財產權人：{holder}</div>' if holder else ''

    notices = ''.join(
        f'<p class="notice">◎{_e(d.get(k))}</p>'
        for k in ('notice_zh', 'notice_en') if d.get(k)
    )

    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>版權頁 {year}</title>{common_style}
<style>
  .page {{ height:296mm; min-height:0; overflow:hidden; padding:38mm 0 0;
           align-items:center; color:#000;
           font-family:'Noto Serif TC','Noto Serif CJK TC',serif;
           font-size:10.5pt; line-height:1.75; }}
  .wrap {{ width:142mm; }}
  .box  {{ border:0.8pt solid #000; padding:5mm 5mm 6mm; }}
  .box-title {{ font-size:12pt; font-weight:700; margin-bottom:4mm; }}
  .row  {{ display:flex; margin-top:1.2mm; }}
  .lab  {{ flex:0 0 auto; display:flex; white-space:nowrap; }}
  .lab .t {{ display:flex; justify-content:space-between; width:4em; }}
  .val  {{ flex:1 1 auto; }}
  .sub  {{ padding-left:2em; }}
  .blk + .blk {{ margin-top:3mm; }}
  .note {{ font-size:8.5pt; line-height:1.7; margin:2mm 0 3mm 1em; }}
  .ids  {{ display:flex; justify-content:space-between; margin-top:3.5mm; }}
  .holder {{ margin-top:4mm; font-size:9.5pt; }}
  .notice {{ font-size:9pt; line-height:1.6; margin:3mm 0 0;
             padding-left:1.4em; text-indent:-1.4em; }}
</style>
</head><body>
<div class="page">
  <div class="wrap">
    <div class="box">
      <div class="box-title">中華民國 {roc_year} 年潮汐觀測資料年報</div>
      {rows}
      {note_html}
      {tail}
    </div>
    {ids_html}
    {holder_html}
    {notices}
  </div>
</div>
</body></html>"""
