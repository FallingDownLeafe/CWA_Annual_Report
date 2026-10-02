"""
station_map.py — 潮位站位置圖（matplotlib 疊在 assets/taiwan_outline.png 上）

    from station_map import render_station_map
    png = render_station_map(conn, station_list, cfg, root, out_png)   # 回傳 Path 或 None

底圖要求：只含海岸線／縣市界／離島與外框，「不含」站點黑點、站名、圖名
        （圖名由 generate_report._html_image_page 另外加）。

經緯度 → 底圖像素 的轉換是 6 參數仿射（可含輕微旋轉）：
    px = a*lon + b*lat + c
    py = d*lon + e*lat + f
預設係數為「初估值」（用舊圖黑點與各站經緯度概估，誤差約 3~7 px）。
正式校準請在 config.yaml 的 map.control_points 給 ≥3 個「經緯度 ↔ 底圖像素」對應點，
程式改用最小平方法重新求解。map.debug_grid: true 會疊上 0.5° 經緯網格，方便目視檢查。

config.yaml 對應區塊（全部選填）：
    map:
      base_image: assets/taiwan_outline.png     # 缺省取 assets.taiwan_outline
      debug_grid: false
      control_points:                            # [lon, lat, px, py]
        - [120.7714, 24.6514, 283.5, 183.0]
      pixel_overrides:                           # 離島框內、或個別站要手動指定的像素位置
        "1926": [82, 70]                         #   馬祖（左上虛線框）
        "198":  [66, 580]                        #   東沙（左下虛線框）
      label:
        "1516": {side: right, dx: 6, dy: -3}     # 個別站標籤：side = left/right/above/below
      min_gap: 12                                # 同側標籤最小垂直間距（以 568 px 寬底圖為基準，自動按比例縮放）
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

# 中文字型（依序尋找第一個系統有的）
_CJK_FONTS = ['Microsoft JhengHei', 'Microsoft YaHei', 'Noto Sans CJK TC', 'Noto Sans TC',
              'PingFang TC', 'Heiti TC', 'SimHei', 'DejaVu Sans']

# 預設校準（初估）：px = a*lon + b*lat + c ；py = d*lon + e*lat + f
_DEFAULT_AFFINE = np.array([[137.825, -1.241, -16333.474],
                            [-8.460, -140.491, 4671.979]])

# 預設離島框內位置（底圖像素），可被 map.pixel_overrides 覆蓋
_DEFAULT_PIXELS = {'1926': (82.5, 70.0),     # 馬祖（左上框）
                   '198':  (66.0, 580.0)}    # 東沙（左下框）

# 預設標籤側：框內離島、島嶼最北端等
_DEFAULT_SIDE = {'1926': 'right', '198': 'right'}

_INK, _STEEL = '#1D3557', '#457B9D'

# 預設仿射與離島像素是以「寬 568 px」的舊底圖量得；底圖換成別種解析度時，預設值與所有偏移量按比例縮放。
# 使用者自己填的 control_points / pixel_overrides 一律視為「實際底圖像素」，不再縮放。
_REF_W = 568.0

_SQL = """
SELECT s.stid, s.stnac, s.stnae, s.lon, s.lat
FROM   st s
WHERE  s.stid IN ({ph}) AND s.reliable = '1'
  AND  s.kind IN ('6', '9') AND s.sponsor = '中央氣象署'
ORDER  BY CASE s.kind WHEN '6' THEN 0 ELSE 1 END
"""


# ──────────────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────────────
def get_station_points(conn, station_list: list[str]) -> list[dict]:
    """依 station_list 順序回傳 [{'stid','zh','en','lon','lat'}]；缺座標者印警告後略過。"""
    if not station_list:
        return []
    with conn.cursor() as cur:
        cur.execute(_SQL.format(ph=','.join(['%s'] * len(station_list))), tuple(station_list))
        rows = cur.fetchall()
    by_id: dict[str, dict] = {}
    for r in rows:                                    # 同 stid 只取第一筆（kind=6 優先）
        by_id.setdefault(str(r['stid']), r)

    pts = []
    for stid in station_list:
        r = by_id.get(stid)
        if r is None:
            print(f'[station_map] ⚠ {stid} 查無符合條件的 st 資料，略過')
            continue
        if r.get('lon') is None or r.get('lat') is None:
            print(f'[station_map] ⚠ {stid} 沒有經緯度，略過')
            continue
        pts.append({
            'stid': stid,
            'zh':   str(r['stnac'] or stid).removesuffix('潮位站'),
            'en':   str(r['stnae'] or ''),
            'lon':  float(r['lon']),
            'lat':  float(r['lat']),
        })
    return pts


# ──────────────────────────────────────────────────────────────────────────────
# 座標轉換
# ──────────────────────────────────────────────────────────────────────────────
def fit_affine(control_points: list[list[float]]) -> np.ndarray:
    """control_points = [[lon, lat, px, py], ...]（≥3 點）→ 2×3 係數矩陣。"""
    P = np.asarray(control_points, dtype=float)
    if P.ndim != 2 or P.shape[1] != 4 or len(P) < 3:
        raise ValueError('map.control_points 需要至少 3 組 [lon, lat, px, py]')
    A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
    cx = np.linalg.lstsq(A, P[:, 2], rcond=None)[0]
    cy = np.linalg.lstsq(A, P[:, 3], rcond=None)[0]
    res = np.hypot(A @ cx - P[:, 2], A @ cy - P[:, 3])
    print(f'[station_map] 校準：{len(P)} 點，殘差平均 {res.mean():.1f} px、最大 {res.max():.1f} px')
    return np.vstack([cx, cy])


def _to_px(aff: np.ndarray, lon: float, lat: float) -> tuple[float, float]:
    v = aff @ np.array([lon, lat, 1.0])
    return float(v[0]), float(v[1])


# ──────────────────────────────────────────────────────────────────────────────
# 標籤配置
# ──────────────────────────────────────────────────────────────────────────────
def _auto_side(p: dict) -> str:
    """
    西岸／澎湖（lon < 121.0）放左側；北海岸西段（淡海、淡水、竹圍、麟山鼻：
    lat ≥ 25.10 且 lon < 121.6）也放左側，避免壓到基隆一帶；其餘（東岸、基隆以東）放右側。
    """
    if p['stid'] in _DEFAULT_SIDE:
        return _DEFAULT_SIDE[p['stid']]
    if p['lon'] < 121.0 or (p['lat'] >= 25.10 and p['lon'] < 121.6):
        return 'left'
    return 'right'


def _spread(items: list[dict], min_gap: float) -> None:
    """同側標籤由上往下排序，太近就把後者往下推（就地修改 item['ly']）。"""
    items.sort(key=lambda it: it['ly'])
    for prev, cur in zip(items, items[1:]):
        if cur['ly'] - prev['ly'] < min_gap:
            cur['ly'] = prev['ly'] + min_gap


# ──────────────────────────────────────────────────────────────────────────────
# 繪圖
# ──────────────────────────────────────────────────────────────────────────────
def render_station_map(conn, station_list: list[str], cfg: dict,
                       root: Path, out_png: Path) -> Path | None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from PIL import Image

    mcfg = cfg.get('map', {}) or {}
    base_rel = mcfg.get('base_image') or cfg.get('assets', {}).get('taiwan_outline')
    base_path = Path(root) / base_rel if base_rel else None
    if not base_path or not base_path.exists():
        print(f'[station_map] ⚠ 找不到底圖（{base_rel}），跳過測站位置圖')
        return None

    pts = get_station_points(conn, station_list)
    if not pts:
        print('[station_map] ⚠ 沒有可畫的測站，跳過')
        return None

    img = Image.open(base_path).convert('RGB')
    W, H = img.size
    k = W / _REF_W                                    # 底圖相對 568 px 參考寬度的比例

    if mcfg.get('control_points'):
        aff = fit_affine(mcfg['control_points'])
    else:
        aff = _DEFAULT_AFFINE * k
        print('[station_map] 使用預設初估校準（誤差約 3~7 px）；建議在 config.yaml 填 map.control_points')
    pix_over = {kk: (x * k, y * k) for kk, (x, y) in _DEFAULT_PIXELS.items()}
    pix_over.update({str(kk): tuple(v) for kk, v in (mcfg.get('pixel_overrides') or {}).items()})
    lab_over = {str(kk): v for kk, v in (mcfg.get('label') or {}).items()}
    min_gap  = float(mcfg.get('min_gap', 12)) * k

    plt.rcParams['font.sans-serif'] = _CJK_FONTS
    plt.rcParams['axes.unicode_minus'] = False

    # 圖面固定 568/96 吋寬（字級以 pt 計，與底圖解析度無關），輸出 300 dpi
    fig = plt.figure(figsize=(_REF_W / 96, _REF_W / 96 * H / W), dpi=300)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(img, extent=(0, W, H, 0), interpolation='lanczos')
    ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis('off')

    # 除錯格線：0.5° 經緯網格
    if mcfg.get('debug_grid'):
        for lon in np.arange(119.0, 123.01, 0.5):
            xs, ys = zip(*[_to_px(aff, lon, la) for la in (21.5, 25.5)])
            ax.plot(xs, ys, color='#E76F51', lw=0.3, alpha=.7)
            ax.text(xs[0], ys[0] + 8 * k, f'{lon:g}E', color='#E76F51', fontsize=4, ha='center')
        for lat in np.arange(21.5, 25.51, 0.5):
            xs, ys = zip(*[_to_px(aff, lo, lat) for lo in (119.0, 123.0)])
            ax.plot(xs, ys, color='#E76F51', lw=0.3, alpha=.7)
            ax.text(xs[0] + 2 * k, ys[0] - 2 * k, f'{lat:g}N', color='#E76F51', fontsize=4)

    # 站點像素位置 + 標籤側
    items = []
    for p in pts:
        if p['stid'] in pix_over:
            px, py = pix_over[p['stid']]
        else:
            px, py = _to_px(aff, p['lon'], p['lat'])
        if not (0 <= px <= W and 0 <= py <= H):
            print(f'[station_map] ⚠ {p["stid"]} {p["zh"]} 落在底圖範圍外（px={px:.0f}, py={py:.0f}），'
                  f'請在 map.pixel_overrides 指定位置')
            continue
        o = lab_over.get(p['stid'], {})
        items.append({**p, 'px': px, 'py': py, 'side': o.get('side') or _auto_side(p),
                      'dx': float(o.get('dx', 7)) * k, 'dy': float(o.get('dy', 0)) * k,
                      'ly': py + float(o.get('dy', 0)) * k})

    for side in ('left', 'right'):                   # 上下側標籤不做避讓
        _spread([it for it in items if it['side'] == side], min_gap)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()

    def _width_px(t) -> float:
        bb = t.get_window_extent(renderer)
        (x0, _), (x1, _) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
        return abs(x1 - x0)

    for it in items:
        ax.plot(it['px'], it['py'], 'o', ms=2.6, color='black', zorder=5)
        side, lx, ly = it['side'], it['px'], it['ly']
        if abs(ly - it['py']) > 5 * k:                   # 標籤被推開時，補一條細引線
            ex = lx + (4 * k if side == 'right' else -4 * k)
            ax.plot([lx, ex], [it['py'], ly], color='#8A97A3', lw=0.3, zorder=4)

        zh_kw = dict(fontsize=8, color=_INK, va='center', fontweight='medium', zorder=6)
        en_kw = dict(fontsize=5.5, color=_STEEL, va='center', zorder=6)
        gap = 2.5 * k
        if side == 'right':
            t_zh = ax.text(lx + it['dx'], ly, it['zh'], ha='left', **zh_kw)
            w = _width_px(t_zh)
            ax.text(lx + it['dx'] + w + gap, ly, it['en'], ha='left', **en_kw)
        elif side == 'left':
            t_en = ax.text(lx - it['dx'], ly, it['en'], ha='right', **en_kw)
            w = _width_px(t_en)
            ax.text(lx - it['dx'] - w - gap, ly, it['zh'], ha='right', **zh_kw)
        else:                                        # above / below：中文在上、英文在下，置中
            sgn = -1 if side == 'above' else 1
            ax.text(lx, ly + sgn * (it['dx'] + 3 * k), it['zh'], ha='center', **zh_kw)
            ax.text(lx, ly + sgn * (it['dx'] + 11 * k), it['en'], ha='center', **en_kw)

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=300, facecolor='white')
    plt.close(fig)
    print(f'[station_map] ✓ {len(items)} 站 → {out_png}')
    return out_png
