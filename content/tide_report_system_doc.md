# 潮汐觀測資料年報 PDF 產生系統 — 系統說明文件

> 範圍：`generate_report.py` 主流程與其呼叫的各報表模組、模板、設定檔。
> 依據：Project FILES 內的程式碼（含 2026-10 頁碼與頁尾修正後的版本）。
> 本文件寫的是「系統現況」；程式碼若有調整，請同步修改本文件。

---

## 1. 系統概述

由資料庫（MySQL `mrbank`）讀取潮位觀測與統計資料，逐站產生四種報表，加上前頁與後頁，合併成一份完整的《潮汐觀測資料年報》PDF，並在合併後的每頁印上頁碼。

**技術組合**：Python（開發環境為 3.13）＋ Jinja2 模板 → HTML → WeasyPrint 轉 PDF；時序圖用 matplotlib 畫成 PNG 再嵌入 HTML；合併與頁碼用 pypdf（檢查頁碼位置用 pypdfium2）。依賴套件版本見 `requirements_freeze.txt`。

**整本書的組成（依 `output_order: by_station` 為例）**

| 順序 | 內容 | 產生方式 |
|---|---|---|
| 1 | 封面 | `generate_report.py::_html_cover` |
| 2 | 目錄 | `_html_toc`（兩次轉換確保頁碼正確） |
| 3 | 說明 | `content/explanation.md` → `explanation_page.py` |
| 4 | 潮位站一覽表 | `station_list.py` + `station_list.html.j2` |
| 5 | 測站位置圖、基準面圖解、台灣潮差分布圖 | 靜態圖（`assets/`，目前為 PDF，原檔直接合併） |
| 6 | 各站：分隔頁 → 逐月統計年報（Report 3）→ 逐日高低潮月報（Report 2）→ 逐時潮位紀錄表（Report 1）→ 潮位時序圖（Report 4） | 各 `reportN_*.py` |
| 7 | 版權頁 | `content/colophon.yaml` → `colophon_page.py` |
| 8 | 封底 | `assets/back_cover.pdf` |

`output_order: by_type` 時改為「章節分隔頁 → 全站同一種報表」，順序依序為 Report 3、2、1、4。

---

## 2. 執行方式

### 2.1 環境設定

1. 專案根目錄放 `.env`（範本見 `_env.example`，實際檔名為 `.env`）：

   ```
   DB_HOST=主機1,主機2        # 可多個，逗號分隔，依序嘗試，第一個連上的使用
   DB_PORT=3306
   DB_NAME=mrbank
   DB_USER=...
   DB_PASSWORD=...
   ```

2. `db.py` 的 `load_env()` 讀取 `.env`（不依賴外部套件）；凍結成 .exe 時讀 .exe 旁邊的 `.env`。連線用 PyMySQL，字元集 `utf8mb3`，`DictCursor`。

### 2.2 主流程指令（PowerShell）

```powershell
$env:PYTHONUTF8 = 1
python generate_report.py --config config.yaml                 # 全站，完整 PDF
python generate_report.py --config config.yaml --stid 1516     # 只處理單站（測試用）
python generate_report.py --config config.yaml --html-only     # 只產 HTML，不轉 PDF（無頁碼）
python generate_report.py --config config.yaml --debug-env     # 印出 .env 讀取詳情
```

- `--stid` 只影響內文報表；前頁（一覽表等）也只會列該站。
- 輸出檔名固定為 `tide_report_{year}.pdf`，每次執行會覆蓋同名檔，需要比較請先自行複製。

### 2.3 各模組可單獨執行（除錯用，產出不含頁碼）

```powershell
python report1_hourly_record.py   --stid 1516 --year 2026 [--month 7] [--html-only]
python report2_daily_hl.py        --stid 1516 --year 2026 [--month 7] [--html-only]
python report3_annual_stats.py    --stid 1516 --year 2026 [--html-only]
python report4_tidal_sequences.py --stid 1516 --year 2026 [--html-only]
python station_list.py            --year 2026 [--html-only]
python moon.py 2026                # 列出該年所有朔、上弦、望、下弦（台灣時間）
```

單獨執行時輸出到 `output/report{N}_{stid}_{year}.html/.pdf`；注意這些腳本內建的 `STATION_INFO` 只是備援，正式流程由資料庫 `st` 表取得測站資訊。

---

## 3. 專案結構

```
generate_report.py        主流程：段落組裝、PDF 轉換、目錄、合併、頁碼
config.yaml               設定檔
db.py                     .env 載入與資料庫連線
report1_hourly_record.py  逐時潮位紀錄表
report2_daily_hl.py       逐日高低潮月報
report3_annual_stats.py   逐月統計年報
report4_tidal_sequences.py 潮位觀測時序圖
station_list.py           潮位站一覽表（也定義全書的測站收錄條件）
station_map.py            測站位置圖（matplotlib 疊圖；目前未啟用）
explanation_page.py       說明頁（解析 explanation.md）
colophon_page.py          版權頁（解析 colophon.yaml）
moon.py                   月相事件精確時刻計算（純標準庫）
templates/                report1/2/3.html.j2、station_list.html.j2
content/                  explanation.md、colophon.yaml（每年改這裡的文字）
assets/                   靜態圖（PDF/PNG）、可選的 NotoSansTC 字型
output/                   最終 PDF；output/html、output/pdf_parts 為中間檔
```

> 注意：模板在實際專案中的檔名是 `templates/report1.html.j2` 等，Project FILES 內顯示為 `report1_html.j2`。

---

## 4. 設定檔 config.yaml

| 區塊 | 說明 |
|---|---|
| `year` | 資料年份（西元）。標題的民國年、刊期由它換算 |
| `stations.auto` | `true`：自動依收錄條件從資料庫抓全部測站，依 `stid` 數值排序；`false`：用 `stations.list` 的明列順序 |
| `stations.list` | 明列站碼（字串）。不符收錄條件者會被略過並印警告 |
| `output_order` | `by_station`（每站四份集中）或 `by_type`（依報表類型集中） |
| `toc_level` | `station`：目錄只列前頁與各站（by_type 時各類型）；`full`：每份報表都列。`station` 需要 `include.station_separator: true`，否則自動改用 `full` |
| `include.*` | 各頁面開關：`cover`、`toc`、`explanation`、`station_table`、`station_map`、`reference_level`、`tidal_range_map`、`report1`～`report4`、`station_separator`、`back_cover`、`page_numbers`；另外程式讀取但設定檔未列出、預設為開的有 `taiwan_outline_original`（測站位置圖）、`colophon` |
| `cover.bg_image` | 封面背景圖；檔案不存在則用素色封面 |
| `assets.*` | 靜態圖路徑：`taiwan_outline_original`、`reference_level`、`tidal_range`、`back_cover`。副檔名 `.pdf` 時原檔向量合併；其他圖檔則包成圖頁 |
| `content.explanation_md` | 說明文字檔，預設 `content/explanation.md` |
| `content.colophon_yaml` | 版權頁設定，預設 `content/colophon.yaml`（設定檔未列出時用預設） |
| `output.filename`、`output.pdf_dir` | 最終檔名、輸出資料夾 |
| `output.html_dir` | 設定檔有，但程式未讀取；實際中間 HTML 固定放 `{pdf_dir}/html` |

`station_map: false`：自動打點的位置圖未啟用，目前「測站位置圖」使用 `assets/taiwan_outline_original.pdf`（人工作圖）。`station_map.py` 與 `map:` 設定區塊保留為備用。

---

## 5. 主流程（generate_report.py::run）

1. **取得測站清單**（`get_station_list`）：套用收錄條件（見 §7）。
2. **產生各段落 HTML**：封面 → 說明 → 一覽表 → 靜態圖頁 →（各站：分隔頁、Report 3/2/1/4）→ 版權頁 → 封底。某一份報表失敗只會印警告並跳過，不中斷整體。
3. **逐段轉 PDF**（WeasyPrint）並記錄頁數；靜態 PDF 不轉檔，由 pypdf 讀取頁數。
4. **目錄兩次轉換**：先假設目錄 1 頁算出各段起始頁碼，轉出目錄後取得實際頁數，若與假設不同就重算並重轉。目錄列出自己（「目錄」，第 2 頁）、前頁與各站（或各類型）分隔頁；版權頁、封底不列入（`no_toc`）。
5. **合併 PDF**：依段落順序合併，再疊印頁碼（§9）。

---

## 6. 資料來源與資料表

| 資料表 | 用途 | 取用條件與單位 |
|---|---|---|
| `tide6` | Report 1、Report 4 的逐時水位 | `qc='Q'` 且 `MIN0` 非空；`MIN0` 為整點水位，單位 mm，報表換算為 cm（四捨五入，見下） |
| `tidehl` | Report 2 逐日高低潮 | `QC='Q'`；`HORL` 為 H/L，`HEIGHT` 單位 mm |
| `tidestat` | Report 3 與 Report 2 右側統計面板、收錄條件判斷 | `SL='S'`（國曆月統計）；`MONTH` 1–12 為月統計、0 為年度彙整；同月有多筆時取 `LAST_UPDATETIME` 最新一筆；`QC='Q'` 資料量足夠，`QC='%'` 資料量不足 2/3 |
| `st` | 測站名稱、位置、經緯度、所屬機構、儀器種類 | 見 §7；主鍵為 (`stid`, `stid_new`, `kind`)，同一測站可有多列 |
| `tiderlkp` | 水準點資訊（`TGBM`、`ZSHIFT`、`TWVD`、`Report`） | 只取 `END IS NULL` 的現行列 |

**設計原則**

- `tide6`／`tidehl` 只取 `qc='Q'` 是刻意設計，不要更動。
- 單位換算 mm → cm 一律使用 Python `round()`（四捨六入五成雙），不改。
- 空白表示沒有觀測或儀器故障，不做內插。
- 統計基礎：Report 1 的日平均只用逐時 `MIN0`；`tidestat` 的月平均潮位 `MWL` 則取 `MIN0`～`MIN9` 的所有非空值合併計算（不做逐日加權），所以月 `MWL` 不等於各日日平均的平均，兩者不會完全吻合。
- 水位浮標的高程已由 TWCD2021 轉換為 TWVD2001，因此報表表頭的 TWVD2001 基準說明仍適用。
- 資料庫欄位定義與範例行見 Project FILES 的三份 DDL 文字檔。

---

## 7. 測站收錄規則

單一來源：`station_list.py::ELIGIBLE_STATION_SQL`，一覽表與主流程共用。條件：

- `st.kind IN ('6','9')`（潮位站、水位浮標）
- `st.reliable = '1'`
- `st.sponsor = '中央氣象署'`
- 該年度 `tidestat` 至少有一個月（`SL='S'`，`MONTH` 1–12）的 `MWL` 非空（早已停測、當年無資料的站不列入）

`get_station_info()` 取單站資訊時，同一 `stid` 有多列則優先取 `kind='6'`，且只取一列。新增 `kind=9` 的 GNSS 站時，必須同時符合以上條件（含當年度 `tidestat` 有資料）才會出現。

**潮位站一覽表欄位規則**

- 儀器型式：`type` 1、2 → 音波式（Sonic），3 → 壓力式（Pressure），4 → 雷達式（Radar）；`kind='9'` 且 `type='1'` → `GNSS`。
- 高程值取自 `tiderlkp.ZSHIFT`（mm 轉 m，三位小數）；`TGBM` 為水準點編號；`TWVD` 非 `Y`（離島，採當地平均海水面 LMSL）者水準點編號加 `*`，並顯示腳注。
- 無高程或水準點資料顯示「尚未公告」；量測年分取自 `tiderlkp.Report`。
- 位置欄過長時自動平均斷行（`_balance_location`，每行上限 `_LOC_MAX_CHARS=9` 字，需與模板欄寬同步調整）。

---

## 8. 各頁面規則

### 8.1 逐時潮位紀錄表（Report 1，A4 橫式，每月一頁）

- 欄位：月相符號、國曆日、農曆月/日、0–23 時水位、日平均。
- 日平均：有效筆數 ≥ `_DAILY_MEAN_MIN_OBS`（16，即應測 24 筆的 2/3）才計算；筆數不足顯示 `※`；全無資料顯示空白。
- 農曆由 `lunardate` 轉換；月相符號由 `moon.py` 以日月視黃經差求根，取朔、上弦、望、下弦所在的台灣日期。
- 負值水位以藍色顯示。

### 8.2 逐日高低潮月報（Report 2，A4 直式，每月一頁）

- 左側主表：每日第 1、2 次高潮與低潮的時間與潮高、潮差。
- 當日有效高低潮筆數 > `_RULE_B_MAX`（4）時整日留白。
- 潮差 = 當日最大高潮 − 最小低潮。
- 右側統計面板取自 `tidestat`（`SL='S'`）：平均海面、平均高/低潮位、平均潮差、最大潮差、最高高潮位與最低低潮位（含日期時間）、最高天文潮。
- `QC='%'`（資料量不足 2/3）時，面板內各數值後加 `*`，頁尾註 2 說明；主表逐日資料不受影響。

### 8.3 逐月統計年報（Report 3，A4 橫式，單頁）

- 12 個月加年度列（`MONTH=0`）；欄位含 MWL、高低潮次數、MHWL、MLWL、MHAT、MLAT、HAT、LAT、HHWL 與 LLWL（日、農曆日、時間、潮高）、MR、MAR、MATR、MTR。
- 農曆日由 `HHWLD`／`LLWLD`（MMDD 整數）轉成「M月D日」。
- `QC='%'` 的月份，數值後加 `*`，整列文字轉灰；缺月份以空白列補齊。

### 8.4 潮位觀測時序圖（Report 4，A4 直式，單頁）

- matplotlib 繪製 12 個月面板（6 列 × 2 欄），全年共用 Y 軸範圍；缺測以 NaN 斷線；月相以向量 marker 畫在面板頂端。
- 輸出為 150 dpi PNG，以 base64 嵌入 HTML 單頁，再由 WeasyPrint 轉 PDF。
- 中文字型搜尋順序：`assets/NotoSansTC-Regular.ttf` 等 → 系統字型（Microsoft JhengHei、PingFang TC、Noto Sans TC…）→ 找不到則中文顯示為方塊並印警告。

### 8.5 前頁與後頁

- **說明**：`content/explanation.md`。`## 編號` 為一條說明（自動編號：中文「一、二…」，英文「1. 2.…」）；`### 9-1 標題` 為子項（自動編號「(1) (2)」）；每行依有無漢字判斷中文或英文，同語言連續行合併成段；可用 `![說明](路徑)` 插圖；`<!-- -->` 為註解。中英文未成對時會印警告。
- **版權頁**：`content/colophon.yaml`。標題年份與刊期（民國年 − 76）自動換算，`issue_no` 填數字則以填寫為準；`publish_date: auto` 取執行當下年月；欄位留空則該行不顯示。
- **分隔頁**：各站（by_type 時為各類型）章節分隔頁，大型中英標題置中。
- **封面**：純文字版，可設 `cover.bg_image`。
- **封底**：`assets/back_cover.pdf` 原檔合併。

---

## 9. 頁碼規則與實作

**規則**

- 全書連續阿拉伯數字，**封面為第 1 頁**（算頁數）。
- 封面、版權頁、封底、各分隔頁（`sep_` 開頭）：算頁數，不印頁碼（目錄仍指向分隔頁）。
- 其餘頁面一律印頁碼，包含目錄與靜態 PDF。
- 目錄頁碼與印出頁碼一致；Adobe 頁碼框的數字也與印出頁碼一致。
- 同時適用 `output_order` 的 by_station／by_type，以及 `toc_level` 的 station／full，目錄多頁亦同。

**實作**（`generate_report.py`）

- 各段先合併為 `pdf_parts/_merged_nonum.pdf`，再由 `stamp_page_numbers()` 用 pypdf 疊印後輸出最終檔。疊印不改各段版面，故頁數不變、目錄不失準。
- 頁碼依合併順序累計，使用的是與目錄相同的各段 `page_count`。合併後實際總頁數與各段頁數加總不符時中止。
- 樣式：Helvetica（PDF 內建字型，免內嵌），8 pt，深藍 `#1D3557`，底部置中，基線距紙邊 3.6 mm。常數見 `_PN_FONT_PT`、`_PN_BASELINE_MM`、`_PN_COLOR`、`_PN_NO_KEYS`。
- 位置依各頁 CropBox 與 `/Rotate` 換算，直式、橫式、其他尺寸、旋轉頁皆適用。
- 做法是在原內容串前後各加一段新的 content stream，不修改原內容串（來源 PDF 多頁共用同一內容串也不會互相影響）。
- 疊印前以 pypdfium2 檢查頁碼位置是否已有內容；有則只印警告（列出頁碼），不自行處理。
- `include.page_numbers: false` 可關閉。

**已知限制**

- 只印純數字，不能印「第 n 頁」（不使用中文字型）。
- 個別報表腳本單獨執行與 `--html-only` 沒有頁碼；`pdf_parts/` 內各段也沒有頁碼，只有最終 PDF 有。
- 疊印是直接蓋在頁面上；若某靜態 PDF 底部中央已有內容會重疊，需換圖或調整常數。
- 使用 pypdf 的 `PdfWriter._add_object`（非公開 API）；升級 pypdf 大版本後需重新確認。

---

## 10. 頁尾規範（Report 1–4）

- 右側：`{站名}潮位站　Tidal Observation Data Annual Report {年份}`；左側留空；中央留給頁碼。
- 字級統一為 6 px（= 4.5 pt）：Report 1、2、3 為 CSS 6px，Report 4 為 matplotlib `fontsize=4.5`。調整時四處要一起改。
- 不使用「中華民國／民國」字樣。前頁（一覽表、圖頁、說明、目錄）的頁尾只有右側的英文標題。
- Report 3 的頁尾用 `@page` 的 `@bottom-right` 邊界盒（WeasyPrint），其餘用一般頁尾元素。

---

## 11. 年度作業流程（換年度時）

1. 修改 `config.yaml` 的 `year`；確認 `stations` 清單或 `auto`。
2. 修改 `content/colophon.yaml`（網址、電話、展售處、定價、`publish_date` 等）。
3. 檢查 `content/explanation.md` 文字是否需更新。
4. 檢查 `assets/` 靜態圖與封面背景圖是否需更新。
5. 先跑單站測試：`--stid 1516`。確認版面、目錄與頁碼，再跑全站。
6. 全站各跑一次 `by_station` 與 `by_type`，確認目錄頁碼；`toc_level: full` 時目錄會多頁，需單獨檢查。
7. 用 Adobe 抽查：目錄指向的頁 = Adobe 頁碼框 = 印出頁碼。

**選用檢查工具**（不屬於專案正式檔案）：`check_page_numbers.py`。

```powershell
python check_page_numbers.py output\tide_report_2024.pdf [--toc-pages N]
```

檢查每頁印出的數字是否等於實際頁序、列出沒有頁碼的頁（應只有封面、分隔頁、版權頁、封底），並核對目錄每一列。`--toc-pages` 為目錄實際佔的頁數，預設 1；`toc_level: full` 或站數多、目錄超過一頁時要加（頁數可看合併時印出的 `[頁碼]` 摘要中 `toc` 那行，例如 `2–4  toc` 就是 3 頁）。

---

## 12. 疑難排解與已知問題

| 現象 | 可能原因／處理 |
|---|---|
| `⚠ …不符收錄條件…已略過` | 站碼不在 `st`，或 `kind`、`reliable`、`sponsor` 不符，或當年度 `tidestat` 無資料 |
| 某份報表「失敗，跳過」 | 該站該年度查無資料；看終端機訊息中的 `[reportN] 查無資料` |
| Report 4 中文顯示方塊 | 找不到中文字型；將 `NotoSansTC-Regular.ttf` 放進 `assets/`，或安裝系統字型 |
| `[頁碼] ⚠ …頁碼位置已有內容` | 列出的頁底部中央已有內容（多半為靜態 PDF）；需調整該頁內容或頁碼位置常數 |
| `[頁碼] 合併後共 N 頁，但各段頁數加總為 M` | 某段 PDF 的實際頁數與轉換時記錄的不同；重跑，仍發生則檢查該段 |
| 一覽表高程為「尚未公告」 | `tiderlkp` 現行列（`END IS NULL`）沒有 `ZSHIFT` 或沒有現行列 |
| 一覽表出現重複站 | `tiderlkp` 同一 `STID` 有多筆 `END IS NULL`；需修正資料 |

**已知問題／待處理**

- 全站版潮位站一覽表第二頁底部出現陰影，單站測試看不到。懷疑原因：`station_list.html.j2` 的 `@media print { .page { margin:0; box-shadow:none } }` 寫在 `.page` 規則之前，被後面的 `.page`（含 `box-shadow`、`margin:14px auto`、`min-height:297mm`）覆蓋；`report1.html.j2`、`report2.html.j2` 有相同寫法。待追查檔案：`station_list.html.j2`、`report1.html.j2`、`report2.html.j2`。
- 各報表程式中的舊 `astral` 月相邏輯與 `generate_report.py` 的舊版 `_html_explanation` 為死碼（`return` 之後不會執行），可日後清除。
- 模板以 Google Fonts 連結載入字型；無網際網路的內網環境會退回系統字型，字型可能與有網路時不同。
- 目前未打包成 .exe；若打包，需確認 `.env`、`assets/`、`content/`、`templates/` 與字型的相對路徑。
