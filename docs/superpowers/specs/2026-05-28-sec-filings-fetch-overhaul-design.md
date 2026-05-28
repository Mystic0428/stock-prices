# SEC filings 抓取體系全面化 — Design Spec

- **Date**: 2026-05-28
- **Status**: Approved
- **Scope**: Extend `sec-filings` + `filing-text` commands in `scripts/stock.py`

## 1. Motivation

實際使用三檔個股(AMBQ / KTOS / SYM)做深度分析時,15 個關鍵資料點裡 11 個需要 `web_search`。分類後發現結構性缺口在 SEC EDGAR 三個地方:

| 缺口 | 用例 | 占 web_search 用途 |
|---|---|---|
| 8-K 文字 + 附件(含投資人簡報 PDF) | 業績發布、重編、增發決議 | 6 個 |
| 註冊家族文字(S-1 / S-3 / S-3ASR / 424B*) | 增發案 dilution、IPO prospectus | 2 個 |
| `sec-filings` 沒有日期/類型/Item 篩選 | 「2026 Q1 全部 8-K」要兩步 | 工作流痛點 |

補齊這三處可讓 web_search 依賴從 11 個降到 3-4 個,且剩下的(earnings call transcripts、第三方市調 CAGR)是**結構性無免費來源**,可以乾淨地在報告中標記「二手來源」而不是「我們沒做」。

## 2. Goals

1. `sec-filings` 支援日期區間 / form type / 8-K Item 號篩選
2. `filing-text` 支援 8-K(本體 + 附件)、S 家族、424 家族、DEF 14A
3. `filing-text` 支援 `--accession` / `--date` 鎖定特定 filing(不只「最新一份」)
4. PDF 附件文字抽取(EX-99.2 投資人簡報)
5. `max_chars` 預設依 form 家族調整,並提供 `--max-chars` flag(上限 2M)
6. 既有用法 100% 向後相容

## 3. Non-Goals(本份 spec 不做)

- Earnings call transcripts(無免費權威來源)
- 第三方市調 CAGR(Mordor / Yole / Gartner / IDC / Teal Group;全 paywall)
- 公司新聞稿主動抓取(`news` 命令 GDELT 已有部分覆蓋)
- 10-K Footnotes / VIE notes 結構化抽取(雜亂,先靠 `--full`)
- EDGAR submissions 「files 分頁」深歷史(`recent` 那塊 ~1000 筆已涵蓋 5-10 年)
- 非美股 ticker 處理(用戶只買美股)
- OCR(掃描 PDF,SEC 極少見)
- `scripts/stock.py` 模塊拆分(原本因誤解 web 限制提的,撤回)

## 4. Architecture

**對外 surface — 2 個命令,無新命令**:
```
sec-filings TICKER [--limit N] [--from D] [--to D] [--year Y] [--type T,...] [--item I.I,...]
filing-text  TICKER [--type T] [--section S] [--full]
                    [--exhibit EX] [--date D | --accession A]
                    [--max-chars N] [--list-exhibits]
```

**資料源**:`sec-filings` 從 yfinance 切到 EDGAR submissions API(`data.sec.gov/submissions/CIK{cik}.json`),與 `filing-text` 共用同一份 cache(`edgar_sub_{cik}`,24h TTL,已存在)。

**新增 helpers(全部在 `scripts/stock.py` 內,不切模塊)**:

| Helper | 職責 |
|---|---|
| `_edgar_list_filings(cik, *, types, from_date, to_date, items)` | 回 `[{date, type, accession, primary_doc, items, exhibits_index_url}, ...]`,給 `sec_filings()` 和 `filing_text()` 共用 |
| `_edgar_filing_exhibits(cik, accession)` | 從 filing index 頁解析 `{"EX-99.1": url, ...}` |
| `_extract_section_router(text, form_type, section)` | 依 form family 派發到專用 extractor |
| `_extract_section_10k(text, section)` | 10-K/10-Q regex(現有 `_extract_section` 改名 + 擴充)|
| `_extract_section_prospectus(text, section)` | S-1/S-3/424B* 用 prospectus anchor |
| `_extract_section_def14a(text, section)` | DEF 14A 用 governance anchor |
| `_fetch_doc_text(url)` | 依副檔名 dispatch HTML / PDF |
| `_pdf_to_text(raw_bytes)` | pdfplumber 抽文字 + 表格 + 頁碼標記 |
| `_table_to_markdown(table)` | 二維 list → markdown table |

**依賴新增**:`pdfplumber>=0.10`(寫進 `requirements.txt`)。

**快取**:既有 `edgar_doc_{accession}_{filename}` 7 天 cache 沿用;PDF 額外加 `edgar_doc_text_{accession}_{filename}` cache 抽完的文字(避免重抽,改抽取邏輯時重抓 SEC)。

## 5. `sec-filings` 擴充

### CLI
```bash
sec-filings TICKER [--limit N] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--year YYYY]
                   [--type T,T,...] [--item I.I,...]
```

### Flag 語意

| Flag | 意義 | 預設 |
|---|---|---|
| `--limit N` | 結果筆數上限(篩選後)| 20 |
| `--from D` / `--to D` | 日期區間(`filingDate` 比對)| 無 |
| `--year Y` | 等同 `--from Y-01-01 --to Y-12-31` | - |
| `--type T,T` | form type 多選(`8-K,S-3ASR,424B5`),case-insensitive,`/` 保留 | 全部 |
| `--item I.I` | 8-K Item 號多選(`2.02,4.02`)| 無 |

### 行為

- `--type` 自動正規化大小寫(`s-1/a` → `S-1/A`)
- `--item` 從 EDGAR submissions `recent.items[]` 拿(逗號分隔 string)。若該 filing 無 items 欄位 → **排除**(寧可漏抓也不誤報)
- `--year` 與 `--from`/`--to` 互斥
- `--item` 須搭配 `--type` 含 8-K

### 輸出 JSON
```json
{
  "ticker": "KTOS",
  "count": 5,
  "total_available": 87,
  "filter": {"from": "2026-02-01", "to": "2026-03-31", "types": ["8-K"]},
  "filings": [
    {
      "date": "2026-03-02",
      "type": "8-K",
      "title": "Material Definitive Agreement; Other Events",
      "items": ["1.01", "8.01", "9.01"],
      "accession": "0001069258-26-000034",
      "edgar_url": "https://www.sec.gov/Archives/edgar/data/1069258/000106925826000034/",
      "primary_doc_url": "https://www.sec.gov/Archives/edgar/data/1069258/000106925826000034/ktos-20260302.htm",
      "exhibits": {"EX-99.1": "...", "EX-99.2": "...", "EX-99.3": "..."}
    }
  ],
  "note": "..."
}
```

新欄位:`items`、`accession`、`primary_doc_url`、`filter`(回放篩選條件)。`title` 改用 EDGAR `primaryDocDescription`,空時 fallback 到 form type 友善名稱表。

### 錯誤
- `--year` + `--from`/`--to` → `"year and from/to are mutually exclusive"`
- `--item` 沒 `--type 8-K` → `"item filter only valid with --type containing 8-K"`
- `--from > --to` → 報錯
- 無結果 → 回 `{"filings": [], "filter": {...}, "note": "no filings match"}`(空集合法、不報錯)

## 6. `filing-text` 擴充

### CLI
```bash
filing-text TICKER [--type T] [--section S] [--full]
                   [--exhibit EX] [--date D | --accession A]
                   [--max-chars N] [--list-exhibits]
```

### 支援的 form types

| Type | 用途 |
|---|---|
| `10-Q`(default)| 季報 |
| `10-K` | 年報 |
| `8-K` | 重大事件 / 業績發布 |
| `8-K/A` | 8-K 修正 |
| `S-1`, `S-1/A` | IPO / 新證券註冊 |
| `S-3`, `S-3/A`, `S-3ASR` | shelf registration |
| `424B1`-`424B5`, `424B7` | prospectus supplement(實際發行條款)|
| `DEF 14A` | proxy statement(高管薪酬、董事會)|

### Section 抽取對照表

| Form 家族 | 可用 sections | regex anchor 範例 |
|---|---|---|
| `10-K` | `mda` / `business` / `risk` / `properties` / `legal` | `Item 7. MD&A`, `Item 1. Business`, `Item 1A. Risk Factors` |
| `10-Q` | `mda` / `risk` | `Item 2. MD&A`, `Item 1A.` |
| `8-K` | 無 section(以 Item 號或 exhibit 取);無 section 時直接回本體全文 | - |
| `S-*`, `424B*` | `risk` / `use-of-proceeds` / `dilution` / `capitalization` / `underwriting` / `plan-of-distribution` / `business` / `summary` | `RISK FACTORS`, `USE OF PROCEEDS`, `DILUTION`, `CAPITALIZATION`, `UNDERWRITING`, `PLAN OF DISTRIBUTION`(全大寫 anchor) |
| `DEF 14A` | `compensation` / `directors` / `transactions` | `EXECUTIVE COMPENSATION`, `DIRECTOR COMPENSATION`, `RELATED PARTY TRANSACTIONS` |

Invalid `(type, section)` 組合 → 報錯訊息含「valid sections for X: ...」。

### Filing 選擇邏輯

| 給定 | 行為 |
|---|---|
| 只給 `--type` | 撈該 type 最新一份(現狀)|
| `--type` + `--date YYYY-MM-DD` | 該日該 type filing;當天無 → 報錯並列出最近 3 個前後日期;當天多份 → 報錯並列出所有 accession,要求改用 `--accession` 挑 |
| `--accession 0001234-26-000056` | 直接鎖定;`--type` 可省略(從 EDGAR 推 type)|

### Exhibit 抓取
```bash
filing-text SYM --type 8-K --date 2026-05-06 --exhibit ex-99.1   # press release HTML
filing-text SYM --type 8-K --date 2026-05-06 --exhibit ex-99.2   # investor deck PDF
filing-text KTOS --accession 0001069258-26-000034 --list-exhibits  # 不抓內容,列出
```

- `--exhibit` 不分大小寫(`ex-99.1` / `EX-99.1` / `99.1` 都接受,正規化成 `EX-99.1`)
- `--exhibit` + `--section` **互斥**(exhibit 是獨立文件)
- `--list-exhibits` 是診斷 mode、不抓內容、回 `{exhibits: {...}}`
- `--full` 在 8-K 上等同預設(8-K 本體本來就短)

### 輸出 JSON
```json
{
  "ticker": "SYM",
  "form": "8-K",
  "filing_date": "2026-05-06",
  "accession": "0001899830-26-000051",
  "items": ["2.02", "9.01"],
  "section": "EX-99.2 (investor presentation)",
  "source_url": "https://www.sec.gov/Archives/edgar/data/1899830/000189983026000051/sym-20260506-q2-deck.pdf",
  "content_type": "pdf",
  "char_count": 42130,
  "truncated": false,
  "truncated_at_page": null,
  "text": "..."
}
```

新欄位:`filing_date`、`accession`、`items`(8-K 才有)、`content_type`(`html` / `pdf`)、`truncated_at_page`(僅 PDF)。

### Section regex miss 處理

不 silent 截斷、不回空字串:
```json
{
  "error": "section 'dilution' not found in this S-3ASR. Try --full to get the whole document, or --list-exhibits.",
  "source_url": "...",
  "form": "S-3ASR",
  "accession": "..."
}
```

## 7. PDF 處理

### `_fetch_doc_text(url)` dispatch
```
.htm, .html, (no ext) → _html_to_text() (現有)
.pdf                  → _pdf_to_text() (新)
其他                  → error: "unsupported document type: .xlsx"
```

### `_pdf_to_text(raw_bytes)`
```python
import pdfplumber, io
def _pdf_to_text(raw):
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            out.append(f"--- Page {i} ---\n{text}")
            for t_idx, table in enumerate(tables, 1):
                out.append(f"\n[Table {i}.{t_idx}]\n" + _table_to_markdown(table))
    return "\n\n".join(out)
```

### 設計選擇
- **頁碼標記**(`--- Page N ---`)讓 Claude 引用可追溯到頁
- **表格獨立抽**:`extract_tables()` 跟 `extract_text()` 分開抽,因為文字流會把表格內容當散亂行
- **`_table_to_markdown`**:簡單 `| col1 | col2 |\n| --- | --- |`,空 cell 用 `-`,不做 cell 合併推斷
- **不做 OCR**:掃描 PDF(`extract_text` 全空)→ 回 `text: ""`, `pdf_text_empty: true`

### Cache 雙層
- `edgar_doc_{accession}_{filename}` — raw bytes,7 天
- `edgar_doc_text_{accession}_{filename}` — 抽出的純文字,7 天(改抽取邏輯時不用重抓 SEC)

## 8. `max_chars` 政策

### 預設值(中庸 — Pro/Max 方案 Claude.ai 都跑得動)

| Form 家族 | 預設 |
|---|---|
| 10-Q/10-K 單一 section | 150,000 |
| 10-K `--full` | 500,000 |
| 8-K 本體 | 50,000 |
| 8-K exhibit HTML | 200,000 |
| 8-K exhibit PDF | 400,000 |
| S-*/424B* 單一 section | 250,000 |
| S-*/424B* `--full` | 500,000 |
| DEF 14A 單一 section | 200,000 |

### `--max-chars` flag
- 永遠優先於預設值
- 上限 **2,000,000**(怕誤 1e9 灌爆 context)
- 超過 cap → argparse 報錯

### 截斷行為
- `text` 截到 `max_chars`
- `char_count` 記原始長度
- `truncated: true` 明確標記
- PDF 額外回 `truncated_at_page: N`(便於下次 `--max-chars` 拉更高)

## 9. 錯誤處理矩陣

| 情況 | 行為 |
|---|---|
| Ticker 不在 EDGAR | `error: "TICKER not found in SEC EDGAR (US filers only)"` |
| 該 type 找不到 filing | `error: "no 8-K filing found matching filters"`, `tried_types: [...]` |
| `--date` 那天無該 type | `error: "no 8-K on 2026-05-06; nearest: 2026-05-05, 2026-05-15"` |
| `--date` 那天該 type 有多份 | `error: "multiple 8-K on 2026-05-06: 0001899830-26-000051, 0001899830-26-000052. Use --accession to pick."` |
| `--accession` 找不到 | `error: "accession ... not found for TICKER"` |
| `--exhibit` 名稱不存在 | `error: "exhibit EX-99.5 not found. Available: EX-99.1, EX-99.2, EX-99.3"` |
| Section regex 抓不到 | `error: "section 'X' not found in this Y. Try --full or --list-exhibits"` |
| PDF 解析失敗 | `error: "failed to extract PDF text: <reason>. source_url: <url>"` |
| PDF 純影像 | `text: ""`, `pdf_text_empty: true`, `note: "scanned images"` |
| SEC 429/403 | retry 1 次 + 回 SEC 原始訊息 |
| Network timeout | error 含 URL 和 timeout 值 |
| `--max-chars > 2,000,000` | argparse 報錯 |

**通則**:所有 error response 帶 `source_url` 和 `accession`(已鎖定到的話);**不 silent 退化**(PDF 失敗不退回 HTML、section miss 不退回全文)。

## 10. 向後相容承諾

- `sec-filings TICKER --limit N` 行為不變(只新增 flags)
- `filing-text TICKER --type 10-K --section mda` 行為不變
- 預設 `max_chars` 從 60k → 150k(更寬鬆,只多不少)
- 兩命令 JSON 輸出只**新增**欄位,不改/刪既有欄位

## 11. Testing Plan

### Offline 純邏輯 tests(100% 可靠、必跑)

Fixtures 放 `scripts/test_fixtures/`,簽進 repo(< 5MB total):

| Fixture | 內容 |
|---|---|
| `ktos_8k_2026-03-02_index.json` | EDGAR submissions JSON 片段 |
| `sym_10q_2026-q2.htm` | 10-Q HTML 截短 |
| `ktos_s3asr_2026-02.htm` | S-3ASR HTML |
| `sym_8k_ex991_2026-05-06.htm` | EX-99.1 業績稿 |
| `sym_8k_ex992_2026-05-06.pdf` | EX-99.2 投資人簡報(完整)|
| `ambq_s1_2024.htm` | S-1 prospectus |
| `def14a_sample.htm` | DEF 14A |
| `corrupt.pdf` | 故意壞的 PDF |
| `scanned.pdf` | 純影像 PDF |

Test cases:
- `test_list_filings_date_filter` / `_item_filter` / `_item_without_items_field`(排除而非 wildcard)
- `test_section_router_10k_*`、`_s1_*`、`_def14a_*`
- `test_section_miss_returns_error_with_hint`
- `test_pdf_extract_with_page_markers` / `_with_tables` / `_corrupt` / `_scanned`
- `test_filing_text_accession_overrides_type`
- `test_filing_text_exhibit_and_section_mutex`
- `test_filing_text_max_chars_per_form_defaults`
- `test_filing_text_max_chars_cap_enforced`(`--max-chars 3000000` argparse 應報錯)
- `test_normalize_type_case_insensitive` / `_exhibit_case_insensitive`

### Live smoke(network-tolerant,失敗 skip)

- `test_live_sec_filings_date_range_real` — KTOS 2026-01-01~2026-05-31 8-K,確認含 2026-03-02
- `test_live_filing_text_8k_exhibit_real` — SYM 2026-05-06 EX-99.1 含 "earnings"
- `test_live_filing_text_pdf_real` — SYM EX-99.2 PDF `char_count > 30000` 且含 "backlog"
- `test_live_filing_text_s3asr_dilution_real` — KTOS S-3ASR dilution section 含 "$" 和 "shares"

寫死的 live ticker:KTOS / SYM(穩定)。AMBQ 改用 NVDA(避免小市值退市風險)。

### Regression

- `test_filing_text_10k_mda_unchanged` — schema 含 `text` `char_count` `truncated` `source_url`
- `test_sec_filings_limit_only_unchanged` — schema 含 `count` `total_available` `filings`
- `test_filing_text_default_form_type_still_10q`

### Fixture 取得

- 真實檔抓回 → 截短到測試夠用最小尺寸
- PDF fixtures 簽進 repo
- `corrupt.pdf`:`dd` 截 50KB PDF 的前 1KB
- `scanned.pdf`:從公開掃描 SEC filing 抓 / 自製

## 12. 實作順序

每步可獨立 commit、可獨立驗證:

1. `requirements.txt` 加 `pdfplumber>=0.10` + smoke 確認可裝
2. `_pdf_to_text()` + `_table_to_markdown()` + fixtures + offline test
3. `_edgar_list_filings()` helper + `sec-filings` 切 EDGAR 源 + 新 flags
4. `filing-text --accession` / `--date` 鎖定特定 filing
5. `filing-text --type 8-K` + `--exhibit` + `--list-exhibits`
6. `filing-text --type S-1/S-3/424B*` + prospectus section regex
7. `filing-text --type DEF 14A` + governance section regex
8. 新預設 `max_chars` 表(中庸版)+ `--max-chars` flag + 2M cap
9. 更新 SKILL.md(精簡:每命令 ≤ 8 行)
10. 跑完整 test suite + live smoke 確認 KTOS/SYM 真實案例

## 13. SKILL.md 文件政策

skill 裡每命令 ≤ 8 行,結構:
```
### filing-text — narrative + exhibits

.venv/bin/python scripts/stock.py filing-text NVDA --type 10-K --section mda
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --exhibit ex-99.2

Types: 10-K, 10-Q, 8-K, 8-K/A, S-1, S-1/A, S-3, S-3/A, S-3ASR, 424B*, DEF 14A
Sections vary by type (run with invalid section to see valid list).
--exhibit fetches 8-K attachments; --list-exhibits to enumerate.
--date / --accession to pick a specific filing.
--max-chars to override default (up to 2,000,000).
```

完整範例矩陣 / section regex 表 / max_chars 表 只在這份 spec 裡,不進 SKILL.md。

## 14. Coverage 預期

完成後對 AMBQ / KTOS / SYM Stage 5 web_search 用途的影響:

| 來源 | 完成前 | 完成後 |
|---|---|---|
| 已支援(10-K/10-Q `filing-text`)| 4 | 4 |
| 新支援(8-K + S 家族 + 附件)| 0 | 8 |
| 結構性無法支援(transcripts / 市調 / 新聞稿)| 11 | 3-4 |

→ web_search 從 11 個降到 3-4 個,且剩下的都明確是「無免費權威來源」,可標記「⚠️ 二手」。
