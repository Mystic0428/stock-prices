# SEC Filings Fetch Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `sec-filings` and `filing-text` commands in `scripts/stock.py` to cover 8-K bodies + exhibits, S-1/S-3/424 prospectuses, DEF 14A, PDF attachments, and EDGAR-side date/type/Item filtering — closing the structural gaps that forced web_search in Stage 5 reports.

**Architecture:** Single-module extension to `scripts/stock.py` (no module split — per user feedback, the constraint is on `SKILL.md` not source files). New helpers: `_edgar_list_filings`, `_edgar_filing_exhibits`, `_fetch_doc_text`, `_pdf_to_text`, `_table_to_markdown`, plus a section-extraction router that dispatches to `_extract_section_10k` / `_extract_section_prospectus` / `_extract_section_def14a`. Test fixtures live in `scripts/test_fixtures/`.

**Tech Stack:** Python 3.10+, stdlib unittest, yfinance (existing), pdfplumber (new for PDF extraction), SEC EDGAR submissions API (`data.sec.gov`).

**Spec reference:** `docs/superpowers/specs/2026-05-28-sec-filings-fetch-overhaul-design.md`

---

## Task 1: Add pdfplumber dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pdfplumber to requirements.txt**

Read current `requirements.txt`, then add the line `pdfplumber>=0.10` at the end. Final file should keep all existing entries and add the new one.

- [ ] **Step 2: Install into project venv**

```bash
cd /Users/daniel/Developer/personal/stock-prices && .venv/bin/pip install -r requirements.txt
```

Expected: pdfplumber and its deps (pdfminer.six, Pillow, cryptography) install successfully.

- [ ] **Step 3: Smoke-test the import**

```bash
.venv/bin/python -c "import pdfplumber; print(pdfplumber.__version__)"
```

Expected: prints a version like `0.11.x` with no traceback.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add pdfplumber for SEC PDF exhibit extraction

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Acquire test fixtures

**Files:**
- Create: `scripts/test_fixtures/.gitkeep`
- Create: `scripts/test_fixtures/ktos_8k_2026-03-02_index.json`
- Create: `scripts/test_fixtures/sym_10q_2026-q2.htm`
- Create: `scripts/test_fixtures/ktos_s3asr_2026-02.htm`
- Create: `scripts/test_fixtures/sym_8k_ex991_2026-05-06.htm`
- Create: `scripts/test_fixtures/sym_8k_ex992_2026-05-06.pdf`
- Create: `scripts/test_fixtures/ambq_s1_2024.htm`
- Create: `scripts/test_fixtures/def14a_sample.htm`
- Create: `scripts/test_fixtures/corrupt.pdf`
- Create: `scripts/test_fixtures/scanned.pdf`

- [ ] **Step 1: Create fixtures dir**

```bash
mkdir -p /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
touch /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures/.gitkeep
```

- [ ] **Step 2: Fetch real SEC docs via curl with proper User-Agent**

SEC requires a contact in the User-Agent per fair-access policy. Use the env var EDGAR_USER_AGENT if set, else default. Run each curl as a separate command:

```bash
cd /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
UA="${EDGAR_USER_AGENT:-stock-prices-tests as80639as@gmail.com}"

# KTOS submissions JSON (for _edgar_list_filings tests)
curl -sS -H "User-Agent: $UA" \
  "https://data.sec.gov/submissions/CIK0001069258.json" -o ktos_submissions_full.json

# Extract just the "recent" filings block to keep fixture small (use jq)
.venv/bin/python -c "
import json
with open('ktos_submissions_full.json') as f:
    data = json.load(f)
# Keep only what tests need
slim = {
    'cik': data['cik'],
    'name': data['name'],
    'filings': {'recent': data['filings']['recent']}
}
with open('ktos_8k_2026-03-02_index.json', 'w') as f:
    json.dump(slim, f, indent=2)
"
rm ktos_submissions_full.json
```

Expected: `ktos_8k_2026-03-02_index.json` exists, contains `filings.recent.form`, `filingDate`, `accessionNumber`, `items`, `primaryDocument` arrays.

Verify:
```bash
.venv/bin/python -c "
import json
d = json.load(open('ktos_8k_2026-03-02_index.json'))
forms = d['filings']['recent']['form']
print(f'Total filings: {len(forms)}, unique types: {sorted(set(forms))[:10]}')
"
```

- [ ] **Step 3: Fetch real HTML/PDF documents and truncate where needed**

For each HTML doc, fetch and truncate to first 80KB + last 40KB (preserves both top anchors and risk-factor end). For PDFs, keep full.

```bash
cd /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
UA="${EDGAR_USER_AGENT:-stock-prices-tests as80639as@gmail.com}"

# SYM 10-Q Q2 2026 (replace URL with real one if known; fallback discovery below)
# Look up SYM's latest 10-Q URL first via the same submissions endpoint
.venv/bin/python -c "
import json, urllib.request
req = urllib.request.Request('https://data.sec.gov/submissions/CIK0001837240.json',
                              headers={'User-Agent': '$UA'})
data = json.loads(urllib.request.urlopen(req).read())
r = data['filings']['recent']
for i, f in enumerate(r['form']):
    if f == '10-Q':
        cik = int(data['cik'])
        acc = r['accessionNumber'][i].replace('-', '')
        doc = r['primaryDocument'][i]
        print(f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}')
        break
" | xargs -I {} curl -sS -H "User-Agent: $UA" {} -o sym_10q_full.htm

# Truncate to 80K + 40K
.venv/bin/python -c "
with open('sym_10q_full.htm', 'rb') as f:
    raw = f.read()
if len(raw) > 120_000:
    out = raw[:80_000] + b'\n<!-- TRUNCATED FOR TEST FIXTURE -->\n' + raw[-40_000:]
else:
    out = raw
with open('sym_10q_2026-q2.htm', 'wb') as f:
    f.write(out)
"
rm sym_10q_full.htm
```

Repeat the same lookup-then-truncate pattern for:
- `ktos_s3asr_2026-02.htm` — find KTOS most-recent S-3ASR, fetch primary doc
- `sym_8k_ex991_2026-05-06.htm` — find SYM 2026-05-06 8-K, list its exhibits, fetch EX-99.1
- `sym_8k_ex992_2026-05-06.pdf` — same 8-K, fetch EX-99.2 (DO NOT truncate; keep full PDF)
- `ambq_s1_2024.htm` — find AMBQ original S-1 (IPO prospectus)
- `def14a_sample.htm` — any company's recent DEF 14A (use NVDA or AAPL for stability)

For exhibits lookup, use the filing index:
```bash
# For SYM 2026-05-06 8-K, the index is at:
# https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001837240&type=8-K&dateb=&owner=include&count=40
# Or directly fetch the filing's index.json:
curl -sS -H "User-Agent: $UA" \
  "https://www.sec.gov/Archives/edgar/data/1837240/<accession-no-dashes>/index.json" | \
  .venv/bin/python -m json.tool
```

- [ ] **Step 4: Create corrupt.pdf**

```bash
cd /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
# Take the first 1KB of a valid PDF
dd if=sym_8k_ex992_2026-05-06.pdf of=corrupt.pdf bs=1024 count=1 2>/dev/null
```

Verify:
```bash
.venv/bin/python -c "
import pdfplumber
try:
    with pdfplumber.open('corrupt.pdf') as p:
        print(f'pages: {len(p.pages)}')
except Exception as e:
    print(f'expected error: {type(e).__name__}: {e}')
"
```
Expected: prints an exception (PdfReadError or similar) — confirms it's corrupt.

- [ ] **Step 5: Create scanned.pdf**

```bash
cd /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
# Create a 1-page PDF with only an image (no text layer) using reportlab + PIL
.venv/bin/pip install reportlab Pillow >/dev/null 2>&1
.venv/bin/python -c "
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image, ImageDraw
img = Image.new('RGB', (400, 100), color='white')
d = ImageDraw.Draw(img)
d.text((10, 40), 'This text is rasterized only', fill='black')
img.save('_tmp_scan.png')
c = canvas.Canvas('scanned.pdf', pagesize=letter)
c.drawImage('_tmp_scan.png', 50, 700, width=400, height=100)
c.showPage()
c.save()
import os; os.remove('_tmp_scan.png')
"
```

Verify:
```bash
.venv/bin/python -c "
import pdfplumber
with pdfplumber.open('scanned.pdf') as p:
    text = p.pages[0].extract_text() or ''
    print(f'extracted text len: {len(text)}')
"
```
Expected: prints `extracted text len: 0` — confirms no text layer.

- [ ] **Step 6: Verify total fixture size**

```bash
du -sh /Users/daniel/Developer/personal/stock-prices/scripts/test_fixtures
```
Expected: under 6MB total.

- [ ] **Step 7: Commit fixtures**

```bash
cd /Users/daniel/Developer/personal/stock-prices
git add scripts/test_fixtures/
git commit -m "test: add SEC filing fixtures for filing-text extension

KTOS submissions JSON, SYM 10-Q/8-K HTML+PDF, KTOS S-3ASR,
AMBQ S-1, sample DEF 14A, plus corrupt.pdf and scanned.pdf
for error-path tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: PDF extraction helpers (`_table_to_markdown`, `_pdf_to_text`, `_fetch_doc_text`)

**Files:**
- Modify: `scripts/stock.py` (add helpers after the existing `_html_to_text` function — find with `grep -n "_html_to_text" scripts/stock.py`)
- Modify: `scripts/test_stock.py` (add new TestPdfExtraction class)

- [ ] **Step 1: Write failing tests for `_table_to_markdown`**

Add to `scripts/test_stock.py` (find a good place near other helper tests):

```python
import unittest, os
FIXTURES = os.path.join(os.path.dirname(__file__), "test_fixtures")

class TestTableToMarkdown(unittest.TestCase):
    def test_empty_table(self):
        from stock import _table_to_markdown
        self.assertEqual(_table_to_markdown([]), "")

    def test_simple_2x2(self):
        from stock import _table_to_markdown
        out = _table_to_markdown([["Q1", "Q2"], ["100", "120"]])
        self.assertIn("| Q1 | Q2 |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| 100 | 120 |", out)

    def test_none_cells_become_dash(self):
        from stock import _table_to_markdown
        out = _table_to_markdown([["A", None], [None, "B"]])
        self.assertIn("| A | - |", out)
        self.assertIn("| - | B |", out)

    def test_pipe_chars_in_cells_escaped(self):
        from stock import _table_to_markdown
        out = _table_to_markdown([["a|b", "c"]])
        # pipe inside cell must not break columns
        self.assertEqual(out.count("|"), 6)  # 3 separators per row × 2 rows incl header sep
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
cd /Users/daniel/Developer/personal/stock-prices
.venv/bin/python -m unittest scripts.test_stock.TestTableToMarkdown -v
```
Expected: 4 errors (`ImportError: cannot import name '_table_to_markdown'`).

- [ ] **Step 3: Implement `_table_to_markdown`**

Find `_html_to_text` in `scripts/stock.py` and add after it:

```python
def _table_to_markdown(rows):
    """Convert pdfplumber table (list of lists, cells may be None/str) to markdown.

    Empty table → "". Pipe chars in cells are escaped to keep column count stable.
    """
    if not rows:
        return ""
    def cell(c):
        if c is None:
            return "-"
        return str(c).replace("|", "\\|").replace("\n", " ").strip() or "-"
    width = max(len(r) for r in rows)
    norm = [[cell(c) for c in (list(r) + [None] * (width - len(r)))] for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for r in norm[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestTableToMarkdown -v
```
Expected: 4 tests pass.

- [ ] **Step 5: Write failing tests for `_pdf_to_text`**

Add to `scripts/test_stock.py`:

```python
class TestPdfToText(unittest.TestCase):
    def test_normal_pdf_has_page_markers(self):
        from stock import _pdf_to_text
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        text = _pdf_to_text(raw)
        self.assertIn("--- Page 1 ---", text)
        self.assertGreater(len(text), 1000)

    def test_normal_pdf_has_tables_extracted(self):
        from stock import _pdf_to_text
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        text = _pdf_to_text(raw)
        # An investor deck almost certainly has at least one table somewhere
        self.assertIn("[Table ", text)

    def test_corrupt_pdf_raises(self):
        from stock import _pdf_to_text
        with open(os.path.join(FIXTURES, "corrupt.pdf"), "rb") as f:
            raw = f.read()
        with self.assertRaises(Exception):
            _pdf_to_text(raw)

    def test_scanned_pdf_returns_empty_text(self):
        from stock import _pdf_to_text
        with open(os.path.join(FIXTURES, "scanned.pdf"), "rb") as f:
            raw = f.read()
        text = _pdf_to_text(raw)
        # Just page marker, no actual extracted text
        self.assertIn("--- Page 1 ---", text)
        # Strip page markers and whitespace; what's left should be empty
        import re
        body = re.sub(r"--- Page \d+ ---", "", text).strip()
        self.assertEqual(body, "")
```

- [ ] **Step 6: Run tests — confirm failure**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestPdfToText -v
```
Expected: 4 errors (function not defined).

- [ ] **Step 7: Implement `_pdf_to_text`**

Add to `scripts/stock.py` right after `_table_to_markdown`:

```python
import io as _io

def _pdf_to_text(raw_bytes):
    """Extract text from PDF bytes via pdfplumber.

    Output format: "--- Page N ---\\n<page text>\\n[Table N.M]\\n<md table>"
    repeated per page. Scanned PDFs (no text layer) yield only page markers.
    Raises pdfplumber/pdfminer exceptions on corrupt input.
    """
    import pdfplumber
    out = []
    with pdfplumber.open(_io.BytesIO(raw_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            out.append(f"--- Page {i} ---\n{text}")
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            for t_idx, table in enumerate(tables, 1):
                md = _table_to_markdown(table)
                if md:
                    out.append(f"\n[Table {i}.{t_idx}]\n{md}")
    return "\n\n".join(out)
```

- [ ] **Step 8: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestPdfToText -v
```
Expected: 4 tests pass. (`test_normal_pdf_has_tables_extracted` may take ~5 seconds — pdfplumber is slow.)

- [ ] **Step 9: Write failing tests for `_fetch_doc_text`**

```python
class TestFetchDocText(unittest.TestCase):
    def test_html_url_routes_to_html_parser(self):
        from stock import _fetch_doc_text
        # Monkeypatch _edgar_get_html and _html_to_text via the module
        import stock
        orig_html = stock._edgar_get_html
        orig_to_text = stock._html_to_text
        stock._edgar_get_html = lambda url: b"<html><body>hello world</body></html>"
        stock._html_to_text = lambda raw: "hello world" if b"hello" in raw else ""
        try:
            text, ctype = _fetch_doc_text("https://www.sec.gov/foo/bar.htm")
            self.assertEqual(text, "hello world")
            self.assertEqual(ctype, "html")
        finally:
            stock._edgar_get_html = orig_html
            stock._html_to_text = orig_to_text

    def test_pdf_url_routes_to_pdf_parser(self):
        from stock import _fetch_doc_text
        import stock
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        orig = stock._edgar_get_html
        stock._edgar_get_html = lambda url: raw  # serves bytes for any url
        try:
            text, ctype = _fetch_doc_text("https://www.sec.gov/foo/bar.pdf")
            self.assertEqual(ctype, "pdf")
            self.assertIn("--- Page 1 ---", text)
        finally:
            stock._edgar_get_html = orig

    def test_unsupported_extension_raises(self):
        from stock import _fetch_doc_text
        with self.assertRaises(ValueError) as cm:
            _fetch_doc_text("https://www.sec.gov/foo/bar.xlsx")
        self.assertIn("unsupported", str(cm.exception).lower())
```

- [ ] **Step 10: Run tests — confirm failure**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFetchDocText -v
```
Expected: 3 errors.

- [ ] **Step 11: Implement `_fetch_doc_text`**

Add to `scripts/stock.py` right after `_pdf_to_text`:

```python
def _fetch_doc_text(url):
    """Fetch a SEC document and return (text, content_type).

    Dispatches HTML vs PDF by URL extension. Caller handles caching upstream.
    Raises ValueError for unsupported extensions.
    """
    lower = url.lower().split("?")[0]
    if lower.endswith(".pdf"):
        raw = _edgar_get_html(url)  # binary-safe; named *_html for legacy
        return _pdf_to_text(raw), "pdf"
    if lower.endswith((".htm", ".html")) or "." not in lower.rsplit("/", 1)[-1]:
        raw = _edgar_get_html(url)
        if isinstance(raw, bytes):
            return _html_to_text(raw), "html"
        return _html_to_text(raw), "html"
    ext = lower.rsplit(".", 1)[-1]
    raise ValueError(f"unsupported document type: .{ext}")
```

Note on `_edgar_get_html`: confirm it returns bytes (needed for PDF). If it currently returns str only, modify it to return bytes — find with `grep -n "def _edgar_get_html" scripts/stock.py`. If it uses `.read().decode()`, change to return raw `.read()` bytes and have `_html_to_text` decode internally.

- [ ] **Step 12: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFetchDocText TestPdfToText TestTableToMarkdown -v
```
Expected: 11 tests pass.

- [ ] **Step 13: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: add PDF extraction helpers (_pdf_to_text, _table_to_markdown, _fetch_doc_text)

PDF text + table extraction via pdfplumber, with page markers for
provenance. URL extension dispatcher routes HTML vs PDF. Tests cover
normal/corrupt/scanned PDFs and HTML/PDF/unsupported routing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: EDGAR query helpers (`_edgar_list_filings`, `_edgar_filing_exhibits`)

**Files:**
- Modify: `scripts/stock.py` (add after `_edgar_cik` — find with `grep -n "_edgar_cik" scripts/stock.py`)
- Modify: `scripts/test_stock.py` (add TestEdgarListFilings class)

- [ ] **Step 1: Write failing tests for `_edgar_list_filings`**

```python
import json

class TestEdgarListFilings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "ktos_8k_2026-03-02_index.json")) as f:
            cls.sub = json.load(f)

    def _stub_submissions(self, monkey_module):
        # Patch both _edgar_get AND _cached so the cache layer doesn't serve
        # stale data from prior real calls to the same CIK.
        orig_get = monkey_module._edgar_get
        orig_cached = monkey_module._cached
        def stub(url):
            if "submissions" in url:
                return self.sub
            raise AssertionError(f"unexpected URL: {url}")
        monkey_module._edgar_get = stub
        monkey_module._cached = lambda key, ttl, fn: fn()  # bypass cache
        return (orig_get, orig_cached)

    def test_no_filters_returns_all_in_descending_date(self):
        import stock
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            result = stock._edgar_list_filings("0001069258")
            self.assertGreater(len(result), 0)
            # Newest first
            dates = [r["date"] for r in result]
            self.assertEqual(dates, sorted(dates, reverse=True))
            # Each row has the contract fields
            r0 = result[0]
            for key in ("date", "type", "accession", "primary_doc", "items"):
                self.assertIn(key, r0)
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached

    def test_type_filter_8k_only(self):
        import stock
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            result = stock._edgar_list_filings("0001069258", types=["8-K"])
            self.assertTrue(all(r["type"] == "8-K" for r in result))
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached

    def test_date_range_filter(self):
        import stock
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            result = stock._edgar_list_filings("0001069258",
                                               from_date="2026-02-01",
                                               to_date="2026-03-31")
            for r in result:
                self.assertGreaterEqual(r["date"], "2026-02-01")
                self.assertLessEqual(r["date"], "2026-03-31")
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached

    def test_item_filter_excludes_filings_without_items_field(self):
        # Synthesize a stub where some 8-Ks have items, some don't
        import stock
        orig_get = stock._edgar_get
        orig_cached = stock._cached
        fake = {
            "filings": {"recent": {
                "form":            ["8-K",       "8-K",     "8-K"],
                "filingDate":      ["2026-05-06","2026-04-01","2026-03-02"],
                "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
                "primaryDocument": ["a.htm",     "b.htm",     "c.htm"],
                "primaryDocDescription": ["", "", ""],
                "items":           ["2.02,9.01", "",         "1.01,8.01"],
            }}
        }
        stock._edgar_get = lambda url: fake
        stock._cached = lambda key, ttl, fn: fn()
        try:
            result = stock._edgar_list_filings("0000000000",
                                               types=["8-K"], items=["2.02"])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["accession"], "0001-26-1")
            # The 8-K with empty items must be EXCLUDED (not wildcard)
            result2 = stock._edgar_list_filings("0000000000",
                                                types=["8-K"], items=["1.01"])
            self.assertEqual(len(result2), 1)
            self.assertEqual(result2[0]["accession"], "0001-26-3")
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached

    def test_normalize_type_case_insensitive(self):
        import stock
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            r1 = stock._edgar_list_filings("0001069258", types=["8-k"])
            r2 = stock._edgar_list_filings("0001069258", types=["8-K"])
            self.assertEqual(len(r1), len(r2))
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestEdgarListFilings -v
```
Expected: errors / ImportError.

- [ ] **Step 3: Implement `_edgar_list_filings`**

Add to `scripts/stock.py` after `_edgar_cik`:

```python
def _edgar_list_filings(cik, *, types=None, from_date=None, to_date=None, items=None):
    """List filings from EDGAR submissions JSON with optional filters.

    Args:
        cik: zero-padded 10-digit CIK string (or int — will be padded).
        types: iterable of form types, case-insensitive (e.g. ["8-K", "S-3ASR"]).
        from_date / to_date: YYYY-MM-DD strings inclusive.
        items: iterable of 8-K item numbers as strings (e.g. ["2.02", "4.02"]).
               A filing is included only if at least one of its items matches.
               Filings with no items field are EXCLUDED when items filter is set
               (avoid silently returning irrelevant filings).

    Returns: list of dicts with keys date, type, title, accession, primary_doc,
             items, exhibits_index_url. Sorted newest first.
    """
    cik_str = str(cik).zfill(10)
    sub = _cached(f"edgar_sub_{cik_str}", 86400,
                  lambda: _edgar_get(f"https://data.sec.gov/submissions/CIK{cik_str}.json"))
    recent = sub.get("filings", {}).get("recent", {})
    forms       = recent.get("form", [])
    dates       = recent.get("filingDate", [])
    accs        = recent.get("accessionNumber", [])
    primary     = recent.get("primaryDocument", [])
    descrs      = recent.get("primaryDocDescription", [])
    items_arr   = recent.get("items", [])

    types_norm = {t.strip().upper() for t in types} if types else None
    items_set  = {i.strip() for i in items} if items else None
    cik_int = int(cik_str)

    rows = []
    for i, form in enumerate(forms):
        date = dates[i] if i < len(dates) else ""
        if types_norm and form.upper() not in types_norm:
            continue
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        row_items_raw = items_arr[i] if i < len(items_arr) else ""
        row_items = [x.strip() for x in row_items_raw.split(",") if x.strip()]
        if items_set is not None:
            if not row_items:
                continue  # exclude — no items info = can't confirm match
            if not (items_set & set(row_items)):
                continue
        acc = accs[i] if i < len(accs) else ""
        acc_no_dash = acc.replace("-", "")
        doc = primary[i] if i < len(primary) else ""
        descr = descrs[i] if i < len(descrs) else ""
        rows.append({
            "date": date,
            "type": form,
            "title": descr or _FORM_TITLE.get(form, form),
            "accession": acc,
            "primary_doc": doc,
            "items": row_items,
            "primary_doc_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}/{doc}",
            "exhibits_index_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}/",
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


_FORM_TITLE = {
    "10-K": "Annual report",
    "10-Q": "Quarterly report",
    "8-K": "Material event",
    "8-K/A": "Material event (amendment)",
    "S-1": "Registration statement",
    "S-1/A": "Registration statement (amendment)",
    "S-3": "Shelf registration",
    "S-3/A": "Shelf registration (amendment)",
    "S-3ASR": "Automatic shelf registration",
    "DEF 14A": "Proxy statement",
    "DEFA14A": "Additional proxy materials",
    "PRE 14A": "Preliminary proxy statement",
    "424B1": "Prospectus supplement",
    "424B2": "Prospectus supplement",
    "424B3": "Prospectus supplement",
    "424B4": "Prospectus supplement",
    "424B5": "Prospectus supplement",
    "424B7": "Prospectus supplement",
}
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestEdgarListFilings -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Write failing tests for `_edgar_filing_exhibits`**

```python
class TestEdgarFilingExhibits(unittest.TestCase):
    def test_parses_exhibit_map_from_index_json(self):
        import stock
        fake_index = {
            "directory": {
                "name": "/Archives/edgar/data/1069258/000106925826000034",
                "item": [
                    {"name": "ktos-20260302.htm", "type": "8-K"},
                    {"name": "ex991.htm", "type": "EX-99.1"},
                    {"name": "ex992.pdf", "type": "EX-99.2"},
                    {"name": "index.json", "type": ""},
                ],
            }
        }
        orig_get = stock._edgar_get
        orig_cached = stock._cached
        stock._edgar_get = lambda url: fake_index
        stock._cached = lambda key, ttl, fn: fn()
        try:
            ex = stock._edgar_filing_exhibits("1069258", "0001069258-26-000034")
            self.assertIn("EX-99.1", ex)
            self.assertIn("EX-99.2", ex)
            self.assertTrue(ex["EX-99.1"].endswith("ex991.htm"))
            self.assertTrue(ex["EX-99.2"].endswith("ex992.pdf"))
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached
```

- [ ] **Step 6: Run test — confirm failure**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestEdgarFilingExhibits -v
```

- [ ] **Step 7: Implement `_edgar_filing_exhibits`**

Add to `scripts/stock.py` after `_edgar_list_filings`:

```python
def _edgar_filing_exhibits(cik, accession):
    """Parse the filing index.json to build {exhibit_name: url} map.

    Cached 7 days under exhibits_{accession}.
    """
    cik_int = int(str(cik).lstrip("0") or "0")
    acc_no_dash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}"
    index = _cached(f"edgar_exhibits_{acc_no_dash}", 7 * 86400,
                    lambda: _edgar_get(f"{base}/index.json"))
    items = index.get("directory", {}).get("item", [])
    out = {}
    for it in items:
        ex_type = (it.get("type") or "").strip()
        name = it.get("name", "")
        if ex_type and ex_type.upper().startswith("EX-"):
            out[ex_type.upper()] = f"{base}/{name}"
    return out
```

- [ ] **Step 8: Run test — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestEdgarFilingExhibits -v
```

- [ ] **Step 9: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: add _edgar_list_filings + _edgar_filing_exhibits helpers

Direct EDGAR submissions API parsing with type/date/item filters
(items filter excludes filings missing the items field). Exhibit map
built from filing index.json. Shared cache keys with existing
_edgar_sub_/edgar_doc_ pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Refactor `sec_filings()` to use `_edgar_list_filings` + new CLI flags

**Files:**
- Modify: `scripts/stock.py` — `sec_filings()` function and its argparse block
- Modify: `scripts/test_stock.py`

- [ ] **Step 1: Write failing tests for new sec_filings behavior**

```python
class TestSecFilingsExtended(unittest.TestCase):
    def setUp(self):
        import stock
        with open(os.path.join(FIXTURES, "ktos_8k_2026-03-02_index.json")) as f:
            self.sub = json.load(f)
        # Patch CIK resolver, EDGAR fetcher, AND cache bypass (so real cached
        # KTOS submissions on disk don't leak into the test).
        self._orig_cik = stock._edgar_cik
        self._orig_get = stock._edgar_get
        self._orig_cached = stock._cached
        stock._edgar_cik = lambda ticker: ("0001069258", "KRATOS DEFENSE")
        stock._edgar_get = lambda url: self.sub if "submissions" in url else None
        stock._cached = lambda key, ttl, fn: fn()

    def tearDown(self):
        import stock
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._cached = self._orig_cached

    def test_backwards_compat_limit_only(self):
        from stock import sec_filings
        r = sec_filings("KTOS", limit=10)
        self.assertEqual(r["ticker"], "KTOS")
        self.assertIn("count", r)
        self.assertIn("total_available", r)
        self.assertIn("filings", r)
        self.assertLessEqual(r["count"], 10)
        # New required fields per spec
        if r["filings"]:
            f0 = r["filings"][0]
            for k in ("date", "type", "accession", "primary_doc_url"):
                self.assertIn(k, f0)

    def test_date_range_filter(self):
        from stock import sec_filings
        r = sec_filings("KTOS", from_date="2026-02-01", to_date="2026-03-31")
        for f in r["filings"]:
            self.assertGreaterEqual(f["date"], "2026-02-01")
            self.assertLessEqual(f["date"], "2026-03-31")
        self.assertEqual(r["filter"]["from"], "2026-02-01")
        self.assertEqual(r["filter"]["to"], "2026-03-31")

    def test_type_filter(self):
        from stock import sec_filings
        r = sec_filings("KTOS", types=["8-K"])
        for f in r["filings"]:
            self.assertEqual(f["type"], "8-K")

    def test_no_match_returns_empty_not_error(self):
        from stock import sec_filings
        r = sec_filings("KTOS", from_date="1990-01-01", to_date="1990-12-31")
        self.assertEqual(r["filings"], [])
        self.assertNotIn("error", r)
```

- [ ] **Step 2: Run tests — confirm some pass (backwards compat) and new ones fail**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestSecFilingsExtended -v
```
Expected: backwards-compat test may currently fail due to missing `primary_doc_url`; date/type tests fail with TypeError (function doesn't accept new kwargs).

- [ ] **Step 3: Rewrite `sec_filings` function**

Find current `def sec_filings(ticker, limit=20):` in `scripts/stock.py` (around line 1684) and REPLACE the entire function with:

```python
def sec_filings(ticker, limit=20, from_date=None, to_date=None,
                types=None, items=None):
    """List recent SEC filings via EDGAR submissions API, with filters.

    Args:
        ticker: stock ticker symbol.
        limit: max filings to return AFTER filtering. Default 20.
        from_date / to_date: YYYY-MM-DD inclusive bounds on filingDate.
        types: list of form types (case-insensitive). E.g. ["8-K", "S-3ASR"].
        items: list of 8-K item numbers. Requires types to include "8-K".
    """
    try:
        cik, name = _edgar_cik(ticker)
        if not cik:
            return {"ticker": ticker.upper(),
                    "error": f"{ticker.upper()} not found in SEC EDGAR (US filers only)"}
        rows = _edgar_list_filings(cik, types=types, from_date=from_date,
                                   to_date=to_date, items=items)
        total = len(rows)
        rows = rows[:limit]

        out = []
        for r in rows:
            cik_int = int(cik.lstrip("0") or "0")
            acc_no_dash = r["accession"].replace("-", "")
            out.append({
                "date": r["date"],
                "type": r["type"],
                "title": r["title"],
                "items": r["items"],
                "accession": r["accession"],
                "edgar_url": r["exhibits_index_url"],
                "primary_doc_url": r["primary_doc_url"],
                "exhibits": {},  # populated on demand via filing-text --list-exhibits
            })

        flt = {}
        if from_date: flt["from"] = from_date
        if to_date:   flt["to"] = to_date
        if types:     flt["types"] = list(types)
        if items:     flt["items"] = list(items)

        return {
            "ticker": ticker.upper(),
            "count": len(out),
            "total_available": total,
            "filter": flt,
            "filings": out,
            "note": ("type 10-K=annual report, 10-Q=quarterly, 8-K=material event. "
                     "Use filing-text --accession to fetch a specific filing's text, "
                     "or --list-exhibits to enumerate exhibits."),
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}
```

- [ ] **Step 4: Update argparse for sec-filings**

Find the `p_sec_f = sub.add_parser("sec-filings", ...)` block (around line 2150) and REPLACE its argument additions with:

```python
    p_sec_f = sub.add_parser("sec-filings",
                             help="Recent SEC filings (10-K, 10-Q, 8-K, ...) with EDGAR/document URLs")
    p_sec_f.add_argument("ticker")
    p_sec_f.add_argument("--limit", type=int, default=20)
    p_sec_f.add_argument("--from", dest="from_date", default=None,
                         help="Filter from this date (YYYY-MM-DD inclusive)")
    p_sec_f.add_argument("--to", dest="to_date", default=None,
                         help="Filter to this date (YYYY-MM-DD inclusive)")
    p_sec_f.add_argument("--year", type=int, default=None,
                         help="Shorthand for --from YYYY-01-01 --to YYYY-12-31 (mutex with --from/--to)")
    p_sec_f.add_argument("--type", dest="types", default=None,
                         help="Comma-separated form types (case-insensitive), e.g. 8-K,S-3ASR")
    p_sec_f.add_argument("--item", dest="items", default=None,
                         help="Comma-separated 8-K Item numbers, e.g. 2.02,4.02 (requires --type containing 8-K)")
```

- [ ] **Step 5: Update sec-filings dispatch**

Find the `elif args.cmd == "sec-filings":` block (around line 2266) and REPLACE with:

```python
    elif args.cmd == "sec-filings":
        # Validate flag combinations
        from_d, to_d = args.from_date, args.to_date
        if args.year is not None:
            if from_d or to_d:
                print(json.dumps({"error": "--year and --from/--to are mutually exclusive"}))
                return
            from_d, to_d = f"{args.year}-01-01", f"{args.year}-12-31"
        if from_d and to_d and from_d > to_d:
            print(json.dumps({"error": "--from must be <= --to"}))
            return
        types = [t.strip() for t in args.types.split(",")] if args.types else None
        items = [i.strip() for i in args.items.split(",")] if args.items else None
        if items and not (types and any(t.upper() == "8-K" for t in types)):
            print(json.dumps({"error": "--item filter only valid with --type containing 8-K"}))
            return
        result = sec_filings(args.ticker, limit=args.limit, from_date=from_d,
                             to_date=to_d, types=types, items=items)
```

- [ ] **Step 6: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestSecFilingsExtended -v
```
Expected: 4 tests pass.

- [ ] **Step 7: CLI smoke test**

```bash
.venv/bin/python scripts/stock.py sec-filings KTOS --type 8-K --from 2026-02-01 --to 2026-03-31 --limit 5
```
Expected: JSON output with `filings` array containing 8-Ks in that range, including a `2026-03-02` filing.

- [ ] **Step 8: Verify mutex errors**

```bash
.venv/bin/python scripts/stock.py sec-filings KTOS --year 2026 --from 2026-01-01
```
Expected: `{"error": "--year and --from/--to are mutually exclusive"}`

```bash
.venv/bin/python scripts/stock.py sec-filings KTOS --item 2.02
```
Expected: `{"error": "--item filter only valid with --type containing 8-K"}`

- [ ] **Step 9: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: sec-filings supports --from/--to/--year/--type/--item filters

Backed by _edgar_list_filings (direct EDGAR submissions API), no
more yfinance for this command. New JSON fields: items, accession,
primary_doc_url, filter (echoed filter conditions). Backwards
compatible: --limit-only behavior unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Refactor `filing_text()` to dispatch via section router (10-K/10-Q only, preserve behavior)

**Files:**
- Modify: `scripts/stock.py` — `filing_text()`, add `_extract_section_router`, rename `_extract_section` → `_extract_section_10k`

- [ ] **Step 1: Write a regression test that LOCKS current behavior**

```python
class TestFilingTextRegression(unittest.TestCase):
    """Behavior of filing-text 10-K/10-Q must NOT change in refactor."""

    def setUp(self):
        import stock
        self._orig_cik = stock._edgar_cik
        self._orig_get = stock._edgar_get
        self._orig_get_html = stock._edgar_get_html
        self._orig_cached = stock._cached
        stock._cached = lambda key, ttl, fn: fn()

    def tearDown(self):
        import stock
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._edgar_get_html = self._orig_get_html
        stock._cached = self._orig_cached

    def test_10q_mda_default_unchanged(self):
        import stock
        stock._edgar_cik = lambda t: ("0001837240", "SYMBOTIC")
        with open(os.path.join(FIXTURES, "sym_10q_2026-q2.htm"), "rb") as f:
            doc_bytes = f.read()
        # Stub submissions to return a fake with one 10-Q
        stock._edgar_get = lambda url: {
            "filings": {"recent": {
                "form": ["10-Q"],
                "filingDate": ["2026-05-06"],
                "accessionNumber": ["0001-26-1"],
                "primaryDocument": ["sym-10q.htm"],
                "primaryDocDescription": ["10-Q"],
                "items": [""],
            }}
        }
        stock._edgar_get_html = lambda url: doc_bytes
        r = stock.filing_text("SYM")  # defaults: --type 10-Q --section mda
        for k in ("ticker", "form", "source_url", "char_count", "truncated", "text"):
            self.assertIn(k, r)
        self.assertEqual(r["form"], "10-Q")
        self.assertGreater(r["char_count"], 0)
```

- [ ] **Step 2: Run — should currently pass with existing code**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFilingTextRegression -v
```
Expected: PASS (this locks current behavior before we refactor).

- [ ] **Step 3: Rename `_extract_section` → `_extract_section_10k`**

Find `def _extract_section` (around line 1455, just before `def filing_text`). Rename to `_extract_section_10k`. Also rename ALL call sites within `stock.py` (there should be exactly one inside `filing_text`).

After rename, also add the new sections `properties` and `legal` per spec:

```python
def _extract_section_10k(text, form_type, section):
    """Extract a 10-K or 10-Q section by Item-number regex anchor."""
    if section == "mda":
        head = r"Item\s+[27]\.?\s+Management.{0,3}s\s+Discussion\s+and\s+Analysis"
        end = (r"Item\s+7A\.|Item\s+8\." if form_type == "10-K"
               else r"Item\s+3\.|Item\s+4\.")
        return _extract_between(text, head, end)
    if section == "business":  # 10-K only
        return _extract_between(text, r"Item\s+1\.?\s+Business", r"Item\s+1A\.|Item\s+2\.")
    if section == "risk":
        return _extract_between(text, r"Item\s+1A\.?\s+Risk\s+Factors",
                                r"Item\s+1B\.|Item\s+2\.|Item\s+3\.")
    if section == "properties":  # 10-K only
        return _extract_between(text, r"Item\s+2\.?\s+Properties", r"Item\s+3\.")
    if section == "legal":  # 10-K only
        return _extract_between(text, r"Item\s+3\.?\s+Legal\s+Proceedings",
                                r"Item\s+4\.")
    return None
```

- [ ] **Step 4: Add `_extract_section_router` (stub for 8-K / S / DEF 14A)**

Add right after `_extract_section_10k`:

```python
_VALID_SECTIONS = {
    "10-K": {"mda", "business", "risk", "properties", "legal"},
    "10-Q": {"mda", "risk"},
    "8-K":  set(),       # 8-K has no sections; use --exhibit
    "8-K/A": set(),
    "S-1":   {"risk", "use-of-proceeds", "dilution", "capitalization",
              "underwriting", "plan-of-distribution", "business", "summary"},
    "S-1/A": None,  # filled below — same as S-1
    "S-3":   None,
    "S-3/A": None,
    "S-3ASR": None,
    "424B1": None, "424B2": None, "424B3": None, "424B4": None,
    "424B5": None, "424B7": None,
    "DEF 14A": {"compensation", "directors", "transactions"},
    "DEFA14A": {"compensation", "directors", "transactions"},
    "PRE 14A": {"compensation", "directors", "transactions"},
}
# Mirror S-1 sections to all S-* and 424B*
_PROSPECTUS_SECTIONS = _VALID_SECTIONS["S-1"]
for k in list(_VALID_SECTIONS):
    if _VALID_SECTIONS[k] is None:
        _VALID_SECTIONS[k] = _PROSPECTUS_SECTIONS


def _extract_section_router(text, form_type, section):
    """Dispatch section extraction by form family. Returns str or None."""
    family = form_type.upper()
    if family in ("10-K", "10-Q"):
        return _extract_section_10k(text, family, section)
    if family in ("S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR") or family.startswith("424B"):
        return _extract_section_prospectus(text, section)
    if family in ("DEF 14A", "DEFA14A", "PRE 14A"):
        return _extract_section_def14a(text, section)
    if family in ("8-K", "8-K/A"):
        return None  # 8-K has no sections
    return None


def _extract_section_prospectus(text, section):
    """Stub — implemented in Task 8."""
    return None


def _extract_section_def14a(text, section):
    """Stub — implemented in Task 9."""
    return None
```

- [ ] **Step 5: Update `filing_text` to call router (preserve all current behavior for 10-K/10-Q)**

Find `def filing_text` and modify the section-extraction line. Current code:
```python
extracted = _extract_section(text, form_type, section)
```
Change to:
```python
extracted = _extract_section_router(text, form_type, section)
```

- [ ] **Step 6: Run regression — must still pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFilingTextRegression TestEdgarListFilings TestPdfToText TestTableToMarkdown TestFetchDocText TestEdgarFilingExhibits TestSecFilingsExtended -v
```
Expected: all pass.

- [ ] **Step 7: Live smoke**

```bash
.venv/bin/python scripts/stock.py filing-text NVDA --type 10-K --section mda 2>&1 | head -5
```
Expected: JSON with `text` containing MD&A content. (Doesn't error out, char_count > 10000.)

- [ ] **Step 8: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "refactor: filing_text uses section router (no behavior change yet)

Renames _extract_section to _extract_section_10k, adds router that
dispatches 10-K/10-Q to existing logic and S-*/424B*/DEF 14A to stubs
(implemented in subsequent tasks). Adds _VALID_SECTIONS map and two
new 10-K sections (properties, legal). Regression test locks
backwards compatibility.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: filing_text — `--accession`, `--date`, 8-K support, `--exhibit`, `--list-exhibits`

**Files:**
- Modify: `scripts/stock.py` — `filing_text()`, argparse, dispatch
- Modify: `scripts/test_stock.py`

- [ ] **Step 1: Write failing tests**

```python
class TestFilingText8K(unittest.TestCase):
    def setUp(self):
        import stock
        self._orig_cik = stock._edgar_cik
        self._orig_get = stock._edgar_get
        self._orig_get_html = stock._edgar_get_html
        self._orig_cached = stock._cached
        stock._cached = lambda key, ttl, fn: fn()
        stock._edgar_cik = lambda t: ("0001837240", "SYMBOTIC")
        # 8-K filings: two on 2026-05-06, one on 2026-04-01
        self.sub = {
            "filings": {"recent": {
                "form":            ["8-K", "8-K", "8-K"],
                "filingDate":      ["2026-05-06", "2026-05-06", "2026-04-01"],
                "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm"],
                "primaryDocDescription": ["8-K", "8-K", "8-K"],
                "items":           ["2.02,9.01", "5.02", "8.01"],
            }}
        }
        def fake_get(url):
            if "submissions" in url:
                return self.sub
            if "index.json" in url:
                return {"directory": {"item": [
                    {"name": "a.htm", "type": "8-K"},
                    {"name": "ex991.htm", "type": "EX-99.1"},
                    {"name": "ex992.pdf", "type": "EX-99.2"},
                ]}}
            return None
        stock._edgar_get = fake_get
        with open(os.path.join(FIXTURES, "sym_8k_ex991_2026-05-06.htm"), "rb") as f:
            self.html_bytes = f.read()
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            self.pdf_bytes = f.read()
        def fake_html(url):
            if url.endswith(".pdf"):
                return self.pdf_bytes
            return self.html_bytes
        stock._edgar_get_html = fake_html

    def tearDown(self):
        import stock
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._edgar_get_html = self._orig_get_html
        stock._cached = self._orig_cached

    def test_8k_body_default(self):
        import stock
        r = stock.filing_text("SYM", form_type="8-K")
        self.assertEqual(r["form"], "8-K")
        self.assertEqual(r["filing_date"], "2026-05-06")
        # Defaults to FIRST of the two same-day → actually should be ambiguity error
        # See test_date_multiple_same_day_errors below; this test only when --date NOT given
        self.assertIn("text", r)

    def test_date_multiple_same_day_errors(self):
        import stock
        r = stock.filing_text("SYM", form_type="8-K", date="2026-05-06")
        self.assertIn("error", r)
        self.assertIn("multiple", r["error"].lower())
        self.assertIn("0001-26-1", r["error"])
        self.assertIn("0001-26-2", r["error"])

    def test_date_missing_lists_nearest(self):
        import stock
        r = stock.filing_text("SYM", form_type="8-K", date="2026-04-15")
        self.assertIn("error", r)
        self.assertIn("nearest", r["error"].lower())
        self.assertIn("2026-04-01", r["error"])
        self.assertIn("2026-05-06", r["error"])

    def test_accession_overrides_type(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-3")
        self.assertEqual(r["accession"], "0001-26-3")
        self.assertEqual(r["filing_date"], "2026-04-01")

    def test_accession_not_found(self):
        import stock
        r = stock.filing_text("SYM", accession="9999-99-9")
        self.assertIn("error", r)
        self.assertIn("not found", r["error"].lower())

    def test_exhibit_html_fetches_press_release(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.1")
        self.assertEqual(r["content_type"], "html")
        self.assertGreater(r["char_count"], 0)
        self.assertIn("EX-99.1", r["section"])

    def test_exhibit_pdf_fetches_investor_deck(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.2")
        self.assertEqual(r["content_type"], "pdf")
        self.assertIn("--- Page 1 ---", r["text"])
        self.assertIn("EX-99.2", r["section"])

    def test_exhibit_case_insensitive(self):
        import stock
        r1 = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.1")
        r2 = stock.filing_text("SYM", accession="0001-26-1", exhibit="EX-99.1")
        r3 = stock.filing_text("SYM", accession="0001-26-1", exhibit="99.1")
        self.assertEqual(r1["char_count"], r2["char_count"])
        self.assertEqual(r1["char_count"], r3["char_count"])

    def test_exhibit_not_found_lists_available(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.9")
        self.assertIn("error", r)
        self.assertIn("EX-99.1", r["error"])
        self.assertIn("EX-99.2", r["error"])

    def test_exhibit_and_section_mutex(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-1",
                              exhibit="ex-99.1", section="mda")
        self.assertIn("error", r)
        self.assertIn("mutex", r["error"].lower() + " " + "mutually exclusive".lower())

    def test_list_exhibits_mode(self):
        import stock
        r = stock.filing_text("SYM", accession="0001-26-1", list_exhibits=True)
        self.assertIn("exhibits", r)
        self.assertIn("EX-99.1", r["exhibits"])
        self.assertIn("EX-99.2", r["exhibits"])
        self.assertNotIn("text", r)  # list-exhibits doesn't fetch content
```

- [ ] **Step 2: Run — confirm failures**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFilingText8K -v
```
Expected: 11 failures/errors.

- [ ] **Step 3: Rewrite `filing_text` with new signature and logic**

Find current `def filing_text` and REPLACE the entire function with:

```python
def filing_text(ticker, form_type="10-Q", section="mda", full=False, max_chars=None,
                exhibit=None, date=None, accession=None, list_exhibits=False):
    """Fetch and optionally section-extract a SEC filing's text.

    See spec section 6 for full semantics.
    """
    try:
        cik, _ = _edgar_cik(ticker)
        if not cik:
            return {"ticker": ticker.upper(),
                    "error": f"{ticker.upper()} not found in SEC EDGAR (US filers only)"}

        # Mutual exclusions
        if exhibit and section and not _is_default_section(form_type, section):
            return {"ticker": ticker.upper(),
                    "error": "--exhibit and --section are mutually exclusive"}

        # Select filing
        all_filings = _edgar_list_filings(cik)
        target = None
        if accession:
            for r in all_filings:
                if r["accession"] == accession or r["accession"].replace("-", "") == accession.replace("-", ""):
                    target = r
                    break
            if target is None:
                return {"ticker": ticker.upper(),
                        "error": f"accession {accession} not found for {ticker.upper()}"}
        elif date:
            day_matches = [r for r in all_filings
                           if r["date"] == date and r["type"].upper() == form_type.upper()]
            if not day_matches:
                same_type = [r["date"] for r in all_filings
                             if r["type"].upper() == form_type.upper()]
                before = [d for d in same_type if d < date][:1]
                after  = [d for d in reversed(same_type) if d > date][:1]
                nearest = sorted(set(before + after), reverse=True)
                return {"ticker": ticker.upper(),
                        "error": f"no {form_type} on {date}; nearest: {', '.join(nearest) if nearest else 'none'}"}
            if len(day_matches) > 1:
                accs = ", ".join(r["accession"] for r in day_matches)
                return {"ticker": ticker.upper(),
                        "error": f"multiple {form_type} on {date}: {accs}. Use --accession to pick."}
            target = day_matches[0]
        else:
            type_matches = [r for r in all_filings if r["type"].upper() == form_type.upper()]
            if not type_matches:
                return {"ticker": ticker.upper(),
                        "error": f"no {form_type} filing found"}
            target = type_matches[0]

        cik_int = int(cik.lstrip("0") or "0")
        acc_no_dash = target["accession"].replace("-", "")

        # list-exhibits short-circuit
        if list_exhibits:
            exs = _edgar_filing_exhibits(cik, target["accession"])
            return {
                "ticker": ticker.upper(),
                "form": target["type"],
                "filing_date": target["date"],
                "accession": target["accession"],
                "exhibits": exs,
            }

        # Fetch exhibit OR primary doc
        if exhibit:
            exs = _edgar_filing_exhibits(cik, target["accession"])
            ex_key = _normalize_exhibit(exhibit)
            if ex_key not in exs:
                return {"ticker": ticker.upper(),
                        "error": f"exhibit {ex_key} not found. Available: {', '.join(sorted(exs.keys()))}",
                        "accession": target["accession"]}
            url = exs[ex_key]
            section_label = f"{ex_key} ({_exhibit_purpose(ex_key)})"
        else:
            url = target["primary_doc_url"]
            section_label = section if not full else "full document"

        # Fetch and parse
        try:
            raw_text, content_type = _fetch_doc_text(url)
        except Exception as e:
            return {"ticker": ticker.upper(),
                    "error": f"failed to fetch document: {_format_error(e)}",
                    "source_url": url,
                    "accession": target["accession"]}

        # Determine effective max_chars
        if max_chars is None:
            max_chars = _default_max_chars(target["type"], content_type, exhibit, full)

        # Section extraction (only for primary doc, not exhibits)
        if exhibit:
            body = raw_text
        elif full or target["type"].upper() in ("8-K", "8-K/A"):
            body = raw_text  # 8-K body is short; --full equivalent for 8-K
        else:
            extracted = _extract_section_router(raw_text, target["type"], section)
            if extracted is None:
                valid = sorted(_VALID_SECTIONS.get(target["type"].upper(), set()))
                hint = f"valid sections for {target['type']}: {', '.join(valid)}" if valid else "no sections defined for this form type"
                return {"ticker": ticker.upper(),
                        "form": target["type"],
                        "accession": target["accession"],
                        "source_url": url,
                        "error": f"section '{section}' not found in this {target['type']}. {hint}. Try --full or --list-exhibits."}
            body = extracted

        result = {
            "ticker": ticker.upper(),
            "form": target["type"],
            "filing_date": target["date"],
            "accession": target["accession"],
            "items": target["items"] if target["type"].upper() in ("8-K", "8-K/A") else [],
            "section": section_label,
            "source_url": url,
            "content_type": content_type,
            "char_count": len(body),
            "truncated": len(body) > max_chars,
            "text": body[:max_chars],
        }
        if content_type == "pdf":
            # Approximate which page got cut off
            if result["truncated"]:
                import re
                pages_in_truncated = re.findall(r"--- Page (\d+) ---", body[:max_chars])
                result["truncated_at_page"] = int(pages_in_truncated[-1]) + 1 if pages_in_truncated else 1
            else:
                result["truncated_at_page"] = None
            # Scanned-PDF detection
            if not body.strip().replace("--- Page 1 ---", "").strip():
                result["pdf_text_empty"] = True
                result["note"] = "PDF appears to be scanned images — text extraction returned empty"
        return result
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def _is_default_section(form_type, section):
    """True if section is just the argparse default for this form (so user didn't really pick one)."""
    defaults = {"10-Q": "mda", "10-K": "mda"}
    return section == defaults.get(form_type.upper(), None)


def _normalize_exhibit(s):
    """Normalize ex-99.1 / EX-99.1 / 99.1 → EX-99.1."""
    s = s.strip().upper()
    if not s.startswith("EX-"):
        s = "EX-" + s.lstrip("-")
    return s


def _exhibit_purpose(ex_key):
    """Common-case label for known exhibits."""
    return {
        "EX-99.1": "press release",
        "EX-99.2": "investor presentation",
        "EX-99.3": "additional exhibit",
        "EX-10.1": "material contract",
        "EX-2.1":  "merger agreement",
    }.get(ex_key, "exhibit")


def _default_max_chars(form_type, content_type, exhibit, full):
    """Per-spec defaults table (section 8)."""
    f = form_type.upper()
    if exhibit:
        if content_type == "pdf":
            return 400_000
        return 200_000
    if f in ("8-K", "8-K/A"):
        return 50_000
    if f in ("10-Q", "10-K"):
        if full:
            return 500_000 if f == "10-K" else 200_000
        return 150_000
    if f in ("DEF 14A", "DEFA14A", "PRE 14A"):
        return 200_000
    # S-* and 424B*
    if full:
        return 500_000
    return 250_000
```

- [ ] **Step 4: Update argparse and dispatch for `filing-text`**

Find `p_ft = sub.add_parser("filing-text", ...)` (around line 2171) and REPLACE the block with:

```python
    p_ft = sub.add_parser("filing-text",
                          help="Narrative text + exhibits from SEC filings (10-K/10-Q/8-K/S-1/S-3/424/DEF 14A)")
    p_ft.add_argument("ticker")
    p_ft.add_argument("--type", dest="form_type",
                      default="10-Q",
                      help="Filing type (default 10-Q). E.g. 10-K, 8-K, S-3ASR, 424B5, 'DEF 14A'")
    p_ft.add_argument("--section", default="mda",
                      help="Section name; varies by form type. Run with invalid section to see valid list")
    p_ft.add_argument("--full", action="store_true",
                      help="Return the full document text instead of a single section")
    p_ft.add_argument("--exhibit", default=None,
                      help="Fetch a specific exhibit by name (e.g. ex-99.1, EX-99.2, or just 99.1)")
    p_ft.add_argument("--date", default=None,
                      help="Pick filing on a specific date YYYY-MM-DD (must match --type)")
    p_ft.add_argument("--accession", default=None,
                      help="Pick filing by EDGAR accession number (overrides --type/--date)")
    p_ft.add_argument("--max-chars", dest="max_chars", type=int, default=None,
                      help="Override default per-form max chars (hard cap 2,000,000)")
    p_ft.add_argument("--list-exhibits", dest="list_exhibits", action="store_true",
                      help="List exhibits available in the selected filing (no content fetch)")
```

Find `elif args.cmd == "filing-text":` and REPLACE with:

```python
    elif args.cmd == "filing-text":
        if args.max_chars is not None and args.max_chars > 2_000_000:
            print(json.dumps({"error": "--max-chars hard cap is 2,000,000"}))
            return
        result = filing_text(args.ticker, form_type=args.form_type,
                             section=args.section, full=args.full,
                             max_chars=args.max_chars, exhibit=args.exhibit,
                             date=args.date, accession=args.accession,
                             list_exhibits=args.list_exhibits)
```

- [ ] **Step 5: Run new + regression tests**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestFilingText8K TestFilingTextRegression -v
```
Expected: all pass.

- [ ] **Step 6: CLI smoke**

```bash
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --list-exhibits 2>&1 | .venv/bin/python -m json.tool | head -20
```
Expected: JSON listing exhibits of SYM's most recent 8-K.

```bash
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --exhibit ex-99.1 2>&1 | head -c 500
```
Expected: JSON with `content_type: "html"`, populated `text`.

```bash
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --exhibit ex-99.2 2>&1 | head -c 500
```
Expected: JSON with `content_type: "pdf"`, `--- Page 1 ---` somewhere in `text`.

- [ ] **Step 7: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: filing-text supports 8-K + exhibits + --accession/--date selection

New flags: --accession, --date, --exhibit, --list-exhibits, --max-chars.
8-K body fetch + EX-99.* attachment fetch (HTML or PDF). Same-day
disambiguation requires --accession. Exhibit names normalized
case-insensitively. exhibit/section mutex enforced.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Implement `_extract_section_prospectus` for S-1/S-3/424B*

**Files:**
- Modify: `scripts/stock.py`
- Modify: `scripts/test_stock.py`

- [ ] **Step 1: Write failing tests using S-1 and S-3ASR fixtures**

```python
class TestExtractSectionProspectus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "ambq_s1_2024.htm"), "rb") as f:
            cls.s1_text = __import__("stock")._html_to_text(f.read())
        with open(os.path.join(FIXTURES, "ktos_s3asr_2026-02.htm"), "rb") as f:
            cls.s3_text = __import__("stock")._html_to_text(f.read())

    def test_risk_factors_extracted(self):
        from stock import _extract_section_prospectus
        out = _extract_section_prospectus(self.s1_text, "risk")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 5000)
        self.assertIn("risk", out.lower())

    def test_use_of_proceeds_extracted(self):
        from stock import _extract_section_prospectus
        out = _extract_section_prospectus(self.s1_text, "use-of-proceeds")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 100)

    def test_dilution_extracted(self):
        from stock import _extract_section_prospectus
        out = _extract_section_prospectus(self.s1_text, "dilution")
        self.assertIsNotNone(out)
        # Dilution section almost always has a $ sign and "per share"
        self.assertIn("$", out)

    def test_underwriting_extracted_in_s3asr(self):
        from stock import _extract_section_prospectus
        out = _extract_section_prospectus(self.s3_text, "underwriting")
        # S-3ASR may or may not have underwriting itself (often in 424B5);
        # so just assert: result is either None or a real string > 200 chars.
        if out is not None:
            self.assertGreater(len(out), 200)

    def test_invalid_section_returns_none(self):
        from stock import _extract_section_prospectus
        out = _extract_section_prospectus(self.s1_text, "not-a-real-section")
        self.assertIsNone(out)
```

- [ ] **Step 2: Run — confirm failure (stub returns None)**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestExtractSectionProspectus -v
```

- [ ] **Step 3: Implement `_extract_section_prospectus`**

Find the stub `def _extract_section_prospectus(text, section):` and REPLACE with:

```python
def _extract_section_prospectus(text, section):
    """Extract sections from S-1/S-3/424B prospectuses using all-caps anchors.

    Prospectuses use TOC-style headings (RISK FACTORS, USE OF PROCEEDS, etc.).
    End anchors are the next likely section to bound extraction.
    """
    anchors = {
        # section_key: (head_pattern, end_pattern)
        "summary":            (r"PROSPECTUS\s+SUMMARY",
                               r"RISK\s+FACTORS|THE\s+OFFERING|USE\s+OF\s+PROCEEDS"),
        "risk":               (r"RISK\s+FACTORS",
                               r"USE\s+OF\s+PROCEEDS|FORWARD-LOOKING\s+STATEMENTS|CAPITALIZATION|MARKET\s+FOR"),
        "use-of-proceeds":    (r"USE\s+OF\s+PROCEEDS",
                               r"CAPITALIZATION|DILUTION|DIVIDEND\s+POLICY|PRICE\s+RANGE|DETERMINATION\s+OF"),
        "capitalization":     (r"CAPITALIZATION",
                               r"DILUTION|SELECTED\s+|UNAUDITED|MANAGEMENT.S\s+DISCUSSION"),
        "dilution":           (r"\bDILUTION\b",
                               r"SELECTED\s+|MANAGEMENT.S\s+DISCUSSION|UNAUDITED|BUSINESS\b"),
        "underwriting":       (r"\bUNDERWRITING\b",
                               r"LEGAL\s+MATTERS|EXPERTS\b|WHERE\s+YOU\s+CAN|INDEX\s+TO"),
        "plan-of-distribution": (r"PLAN\s+OF\s+DISTRIBUTION",
                                 r"LEGAL\s+MATTERS|EXPERTS\b|WHERE\s+YOU\s+CAN"),
        "business":           (r"^BUSINESS\s*$|BUSINESS\s+OVERVIEW",
                               r"MANAGEMENT\b|EXECUTIVE\s+COMPENSATION|PRINCIPAL\s+STOCKHOLDERS"),
    }
    if section not in anchors:
        return None
    head, end = anchors[section]
    return _extract_between(text, head, end)
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestExtractSectionProspectus -v
```

Expected: 5 tests pass. If anchors don't match the AMBQ S-1 fixture text, adjust regex patterns (this is normal — real prospectuses have layout quirks).

- [ ] **Step 5: Live smoke**

```bash
.venv/bin/python scripts/stock.py filing-text AMBQ --type S-1 --section risk 2>&1 | .venv/bin/python -c "
import json, sys
d = json.load(sys.stdin)
print('form:', d.get('form'), 'char_count:', d.get('char_count'))
print('preview:', d.get('text', '')[:200])
"
```
Expected: form S-1, char_count > 10000, preview shows risk-factor text.

- [ ] **Step 6: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: prospectus section extraction (S-1/S-3/424B*)

7 sections supported: summary, risk, use-of-proceeds, capitalization,
dilution, underwriting, plan-of-distribution, business. All-caps
TOC-style regex anchors with neighbor-section end bounds.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Implement `_extract_section_def14a` for DEF 14A

**Files:**
- Modify: `scripts/stock.py`
- Modify: `scripts/test_stock.py`

- [ ] **Step 1: Write failing tests**

```python
class TestExtractSectionDef14a(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "def14a_sample.htm"), "rb") as f:
            cls.text = __import__("stock")._html_to_text(f.read())

    def test_compensation_extracted(self):
        from stock import _extract_section_def14a
        out = _extract_section_def14a(self.text, "compensation")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 500)

    def test_directors_extracted(self):
        from stock import _extract_section_def14a
        out = _extract_section_def14a(self.text, "directors")
        self.assertIsNotNone(out)

    def test_transactions_extracted(self):
        from stock import _extract_section_def14a
        out = _extract_section_def14a(self.text, "transactions")
        # Some DEF 14As don't have related-party section; allow None
        # but if not None, must be non-trivial
        if out is not None:
            self.assertGreater(len(out), 50)

    def test_invalid_section_returns_none(self):
        from stock import _extract_section_def14a
        self.assertIsNone(_extract_section_def14a(self.text, "not-real"))
```

- [ ] **Step 2: Run — confirm failure**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestExtractSectionDef14a -v
```

- [ ] **Step 3: Implement `_extract_section_def14a`**

Find the stub and REPLACE with:

```python
def _extract_section_def14a(text, section):
    """Extract governance sections from DEF 14A proxy statements."""
    anchors = {
        "compensation": (r"EXECUTIVE\s+COMPENSATION|COMPENSATION\s+DISCUSSION\s+AND\s+ANALYSIS",
                         r"DIRECTOR\s+COMPENSATION|RELATED\s+PARTY|AUDIT\s+COMMITTEE|PROPOSAL\s+\d"),
        "directors":    (r"DIRECTOR\s+COMPENSATION|BOARD\s+OF\s+DIRECTORS",
                         r"RELATED\s+PARTY|AUDIT\s+COMMITTEE|EXECUTIVE\s+OFFICERS|PROPOSAL\s+\d"),
        "transactions": (r"RELATED\s+PARTY\s+TRANSACTIONS|CERTAIN\s+RELATIONSHIPS",
                         r"AUDIT\s+COMMITTEE|EQUITY\s+COMPENSATION|PROPOSAL\s+\d|HOUSEHOLDING"),
    }
    if section not in anchors:
        return None
    head, end = anchors[section]
    return _extract_between(text, head, end)
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestExtractSectionDef14a -v
```

- [ ] **Step 5: Live smoke**

```bash
.venv/bin/python scripts/stock.py filing-text NVDA --type "DEF 14A" --section compensation 2>&1 | .venv/bin/python -c "
import json, sys
d = json.load(sys.stdin)
print('form:', d.get('form'), 'char_count:', d.get('char_count'))
print('preview:', d.get('text', '')[:200])
"
```

- [ ] **Step 6: Commit**

```bash
git add scripts/stock.py scripts/test_stock.py
git commit -m "feat: DEF 14A governance section extraction

3 sections: compensation, directors, transactions. Same all-caps
anchor pattern as prospectus extractor.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Verify `--max-chars` cap and per-form defaults

**Files:**
- Modify: `scripts/test_stock.py`

- [ ] **Step 1: Write tests for max_chars policy**

```python
class TestMaxCharsPolicy(unittest.TestCase):
    def test_defaults_per_form_family(self):
        from stock import _default_max_chars
        # primary doc, not full
        self.assertEqual(_default_max_chars("10-Q", "html", None, False), 150_000)
        self.assertEqual(_default_max_chars("10-K", "html", None, False), 150_000)
        self.assertEqual(_default_max_chars("10-K", "html", None, True),  500_000)
        self.assertEqual(_default_max_chars("10-Q", "html", None, True),  200_000)
        self.assertEqual(_default_max_chars("8-K",  "html", None, False), 50_000)
        self.assertEqual(_default_max_chars("S-1",  "html", None, False), 250_000)
        self.assertEqual(_default_max_chars("S-3ASR", "html", None, True), 500_000)
        self.assertEqual(_default_max_chars("424B5", "html", None, False), 250_000)
        self.assertEqual(_default_max_chars("DEF 14A", "html", None, False), 200_000)
        # exhibit defaults
        self.assertEqual(_default_max_chars("8-K", "html", "EX-99.1", False), 200_000)
        self.assertEqual(_default_max_chars("8-K", "pdf",  "EX-99.2", False), 400_000)

    def test_cap_enforced_via_cli(self):
        import subprocess
        result = subprocess.run(
            [".venv/bin/python", "scripts/stock.py", "filing-text", "NVDA",
             "--max-chars", "3000000"],
            cwd="/Users/daniel/Developer/personal/stock-prices",
            capture_output=True, text=True, timeout=30
        )
        # Should print the cap error to stdout and exit normally
        self.assertIn("2,000,000", result.stdout)
```

- [ ] **Step 2: Run — should already pass given Task 7 implementation**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestMaxCharsPolicy -v
```
Expected: 2 tests pass. If `test_cap_enforced_via_cli` fails because the message wording differs, adjust the assertion to match the exact error message.

- [ ] **Step 3: Commit (tests only, no code change expected)**

```bash
git add scripts/test_stock.py
git commit -m "test: lock in max_chars per-form defaults and 2M CLI cap

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Update SKILL.md (concise per file-size feedback)

**Files:**
- Modify: `SKILL.md` (project root)

- [ ] **Step 1: Read current SKILL.md sec-filings entry and filing-text entry**

```bash
grep -n "^### SEC filings\|^### Filing text\|^### EDGAR" /Users/daniel/Developer/personal/stock-prices/SKILL.md
```

Read both sections (they should be 10-30 lines each currently).

- [ ] **Step 2: Replace the SEC filings entry**

Use Edit tool on `SKILL.md`. Find the current `### SEC filings — regulatory documents` section and its body, replace with:

```markdown
### SEC filings — list with filters

```bash
.venv/bin/python scripts/stock.py sec-filings AAPL --limit 10
.venv/bin/python scripts/stock.py sec-filings KTOS --type 8-K --from 2026-02-01 --to 2026-03-31
.venv/bin/python scripts/stock.py sec-filings SYM --year 2026 --type 8-K --item 2.02
```

Lists filings from EDGAR (newest first): `date`, `type`, `title`, `items` (8-K), `accession`, `primary_doc_url`, `edgar_url`. Filters: `--from`/`--to`/`--year` for date range; `--type` for form types (case-insensitive, comma-separated); `--item` for 8-K Item numbers (requires `--type` containing 8-K). `--year` is mutex with `--from`/`--to`. Use the `accession` value with `filing-text --accession` to fetch a specific filing.
```

- [ ] **Step 3: Replace the Filing text entry**

Find the current `### Filing text — narrative content for analysis` section and its body, replace with:

```markdown
### Filing text — narrative + exhibits from SEC filings

```bash
.venv/bin/python scripts/stock.py filing-text NVDA --type 10-K --section mda
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --exhibit ex-99.2
.venv/bin/python scripts/stock.py filing-text KTOS --type S-3ASR --section dilution
.venv/bin/python scripts/stock.py filing-text SYM --accession 0001899830-26-000051 --list-exhibits
```

Fetches a SEC filing's text from EDGAR and optionally extracts a section.

**Types**: `10-K`, `10-Q`, `8-K`, `8-K/A`, `S-1`, `S-1/A`, `S-3`, `S-3/A`, `S-3ASR`, `424B1-5`, `424B7`, `DEF 14A`.

**Sections vary by type:**
- `10-K`: mda, business, risk, properties, legal
- `10-Q`: mda, risk
- `8-K`: no sections — use `--exhibit` for attachments (EX-99.1 = press release HTML, EX-99.2 = investor deck PDF)
- `S-*` / `424B*`: summary, risk, use-of-proceeds, dilution, capitalization, underwriting, plan-of-distribution, business
- `DEF 14A`: compensation, directors, transactions

**Filing selection:** defaults to latest of `--type`. Use `--date YYYY-MM-DD` to pick a specific day (errors if multiple same-day, suggesting `--accession`), or `--accession` to pin one exactly.

**Exhibits:** `--exhibit ex-99.1` (case-insensitive); `--list-exhibits` enumerates without fetching content. PDFs are extracted via pdfplumber with page markers.

**Char limit:** defaults vary by form (50k for 8-K body up to 500k for 10-K --full). Override with `--max-chars N` (hard cap 2,000,000). Output includes `truncated` and (for PDFs) `truncated_at_page`.

**Errors:** section not found → suggests `--full` or `--list-exhibits`; exhibit not found → lists available; `--date` no match → lists nearest dates. US filers only.
```

- [ ] **Step 4: Verify SKILL.md size hasn't ballooned**

```bash
wc -l /Users/daniel/Developer/personal/stock-prices/SKILL.md
```
Expected: net change < +30 lines vs before (the old sec-filings + filing-text sections were ~30 lines combined; new ones are ~35 lines).

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "docs: update SKILL.md for sec-filings + filing-text extensions

Concise rewrite of both entries — types/sections matrix surfaces
new capabilities (8-K exhibits, S-1/S-3 prospectus, DEF 14A,
date/Item filters) without bloating skill context budget.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Live smoke + final regression validation

**Files:**
- Modify: `scripts/test_stock.py` (add live smoke class if not yet)

- [ ] **Step 1: Add network-tolerant live smoke tests**

```python
class TestLiveSmoke(unittest.TestCase):
    """Network-tolerant tests — skip on connection error, fail on parse error."""

    def _safe_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            self.skipTest(f"network: {e}")

    def test_live_sec_filings_date_range_real(self):
        from stock import sec_filings
        r = self._safe_call(sec_filings, "KTOS", from_date="2026-01-01",
                            to_date="2026-05-31", types=["8-K"])
        if "error" in r and "rate" in r["error"].lower():
            self.skipTest("EDGAR rate limit")
        self.assertGreater(r["count"], 0)
        # The 2026-03-02 8-K should appear
        dates = [f["date"] for f in r["filings"]]
        self.assertIn("2026-03-02", dates)

    def test_live_filing_text_pdf_real(self):
        from stock import filing_text, sec_filings
        # Find SYM's latest 8-K with an EX-99.2 PDF
        listing = self._safe_call(sec_filings, "SYM", types=["8-K"], limit=5)
        if "error" in listing:
            self.skipTest(listing["error"])
        target_acc = None
        for f in listing["filings"]:
            exs_r = filing_text("SYM", accession=f["accession"], list_exhibits=True)
            if "EX-99.2" in exs_r.get("exhibits", {}) and exs_r["exhibits"]["EX-99.2"].endswith(".pdf"):
                target_acc = f["accession"]
                break
        if not target_acc:
            self.skipTest("no SYM 8-K with PDF EX-99.2 in recent filings")
        r = filing_text("SYM", accession=target_acc, exhibit="ex-99.2")
        self.assertEqual(r["content_type"], "pdf")
        self.assertGreater(r["char_count"], 30_000)

    def test_live_filing_text_s_family_real(self):
        from stock import filing_text
        # KTOS S-3ASR may not always exist; try and skip if not
        r = self._safe_call(filing_text, "KTOS", form_type="S-3ASR", full=True)
        if "error" in r and "not found" in r["error"]:
            self.skipTest("KTOS has no S-3ASR currently")
        self.assertIn("text", r)
        self.assertGreater(r["char_count"], 1000)
```

- [ ] **Step 2: Run live smoke**

```bash
.venv/bin/python -m unittest scripts.test_stock.TestLiveSmoke -v
```
Expected: tests pass or skip (with reason); no errors.

- [ ] **Step 3: Run FULL test suite**

```bash
.venv/bin/python scripts/test_stock.py
```
Expected: all green or skipped — no failures or errors.

- [ ] **Step 4: Verify real-world Stage-5 use cases manually**

```bash
# KTOS Q1'26 earnings
.venv/bin/python scripts/stock.py sec-filings KTOS --year 2026 --type 8-K --item 2.02 --limit 3

# SYM Q2'26 investor deck (the original AMBQ-stage-5 motivating case)
.venv/bin/python scripts/stock.py sec-filings SYM --year 2026 --type 8-K --limit 5
# Take an accession from above output, then:
.venv/bin/python scripts/stock.py filing-text SYM --accession <ACC> --list-exhibits
.venv/bin/python scripts/stock.py filing-text SYM --accession <ACC> --exhibit ex-99.2 2>&1 | .venv/bin/python -c "
import json, sys; d = json.load(sys.stdin)
print('chars:', d.get('char_count'), 'truncated:', d.get('truncated'))
print('first 300:', d.get('text','')[:300])
"

# KTOS S-3ASR dilution (the original filing-text question)
.venv/bin/python scripts/stock.py filing-text KTOS --type S-3ASR --section dilution
```

- [ ] **Step 5: Commit live smoke**

```bash
git add scripts/test_stock.py
git commit -m "test: live smoke for SEC filings overhaul

Network-tolerant tests for date-range sec-filings, PDF exhibit
extraction, and S-family fetch against real EDGAR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Final sanity check**

```bash
git log --oneline -15
git status
wc -l scripts/stock.py SKILL.md
```
Expected: ~12 new commits, clean working tree, stock.py grew by ~400-500 lines (still one file, per user constraint clarification).
