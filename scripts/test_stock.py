#!/usr/bin/env python3
"""Smoke + unit tests for stock.py.

Run:  .venv/bin/python -m unittest scripts.test_stock   (from repo root)
  or:  .venv/bin/python scripts/test_stock.py

Most tests are offline (pure logic + file-backed state). A small set of live
tests hit Yahoo Finance and SKIP — rather than fail — on rate-limit/network
errors, so the suite stays green without a connection.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "stock.py")
REPO = os.path.dirname(HERE)
PYTHON = os.environ.get("STOCK_TEST_PYTHON") or os.path.join(REPO, ".venv", "bin", "python")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures")

spec = importlib.util.spec_from_file_location("stock", SCRIPT)
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)


def _series(values, start="2024-01-01"):
    idx = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


class FormatErrorTests(unittest.TestCase):
    def test_generic_exception_does_not_recurse(self):
        # Regression: _format_error used to call itself on the generic path,
        # causing RecursionError for any non-yfinance exception.
        msg = stock._format_error(ValueError("boom"))
        self.assertEqual(msg, "boom")

    def test_rate_limit_text_detected(self):
        msg = stock._format_error(Exception("429 Too Many Requests"))
        self.assertIn("rate limited", msg)


class RoundTests(unittest.TestCase):
    def test_none_passthrough(self):
        self.assertIsNone(stock._round(None))

    def test_rounds(self):
        self.assertEqual(stock._round(1.23456), 1.23)
        self.assertEqual(stock._round(1.23456, 4), 1.2346)


class ReturnsMathTests(unittest.TestCase):
    def test_period_return_simple(self):
        close = _series([100, 101, 102, 110])  # 4 days, +10% over ~3 days
        self.assertAlmostEqual(stock._period_return(close, 3), 10.0, places=1)

    def test_period_return_too_short(self):
        self.assertIsNone(stock._period_return(_series([100]), 3))

    def test_ytd_return(self):
        close = _series([100, 150], start="2024-01-02")  # +50% within the year
        self.assertAlmostEqual(stock._ytd_return(close), 50.0, places=1)

    def test_compute_returns_has_all_horizons(self):
        close = _series(list(range(100, 200)))
        out = stock._compute_returns(close)
        for k in ("1d", "1w", "1mo", "ytd", "1y"):
            self.assertIn(k, out)


class RsiTests(unittest.TestCase):
    def test_rsi_in_range(self):
        rng = np.random.default_rng(0)
        close = _series(100 + np.cumsum(rng.normal(0, 1, 200)))
        rsi = stock._rsi(close, 14).dropna()
        self.assertTrue((rsi >= 0).all() and (rsi <= 100).all())


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = stock.CACHE_DIR
        stock.CACHE_DIR = os.path.join(self.tmp, "cache")

    def tearDown(self):
        stock.CACHE_DIR = self._orig

    def test_stores_and_returns_cached(self):
        calls = []

        def fn():
            calls.append(1)
            return {"value": 42}

        self.assertEqual(stock._cached("k", 60, fn)["value"], 42)
        self.assertEqual(stock._cached("k", 60, fn)["value"], 42)  # served from cache
        self.assertEqual(len(calls), 1)

    def test_expired_refetches(self):
        self.assertEqual(stock._cached("k", -1, lambda: {"v": 1})["v"], 1)
        self.assertEqual(stock._cached("k", -1, lambda: {"v": 2})["v"], 2)

    def test_error_dicts_not_cached(self):
        calls = []

        def fn():
            calls.append(1)
            return {"error": "nope"}

        stock._cached("e", 60, fn)
        stock._cached("e", 60, fn)
        self.assertEqual(len(calls), 2)  # never cached, so called twice


class FileStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = stock.DATA_DIR
        stock.DATA_DIR = self.tmp

    def tearDown(self):
        stock.DATA_DIR = self._orig

    def test_watchlist_lifecycle(self):
        stock.watchlist("add", ["AAPL", "MSFT"])
        stock.watchlist("add", ["AAPL"])  # dedupe
        data = stock._load_json("watchlist.json", {})
        self.assertEqual(data["tickers"], ["AAPL", "MSFT"])
        stock.watchlist("remove", ["AAPL"])
        self.assertEqual(stock._load_json("watchlist.json", {})["tickers"], ["MSFT"])
        stock.watchlist("clear")
        self.assertEqual(stock._load_json("watchlist.json", {})["tickers"], [])

    def test_portfolio_upsert(self):
        stock.portfolio("add", "AAPL", 10, 150)
        stock.portfolio("add", "AAPL", 20, 160)  # upsert updates in place
        positions = stock._load_json("portfolio.json", {})["positions"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["shares"], 20)
        self.assertEqual(positions[0]["cost_basis"], 160)


class CleanAndRecordsTests(unittest.TestCase):
    def test_clean_nan_to_none(self):
        self.assertIsNone(stock._clean(float("nan")))
        self.assertIsNone(stock._clean(np.float64("nan")))

    def test_clean_numpy_natives(self):
        self.assertEqual(stock._clean(np.int64(5)), 5)
        self.assertEqual(stock._clean(np.float64(1.5)), 1.5)
        self.assertIs(stock._clean(np.bool_(True)), True)

    def test_df_records_with_index_and_nan(self):
        df = pd.DataFrame({"a": [1.0, float("nan")], "b": [2, 3]},
                          index=["x", "y"])
        recs = stock._df_records(df, index_as="k")
        self.assertEqual(recs[0], {"k": "x", "a": 1.0, "b": 2})
        self.assertIsNone(recs[1]["a"])  # NaN -> None

    def test_df_records_empty(self):
        self.assertEqual(stock._df_records(pd.DataFrame()), [])


class OfflineErrorPathTests(unittest.TestCase):
    def test_screen_unknown_lists_available(self):
        # Validates against the predefined list before any network call.
        out = stock.screen("definitely_not_a_screener")
        self.assertIn("error", out)
        self.assertTrue(out["available_screeners"])

    def test_sector_unknown_lists_valid(self):
        out = stock.sector("notarealsector")
        self.assertIn("error", out)
        self.assertEqual(len(out["valid_sectors"]), 11)

    def test_screen_custom_requires_filters(self):
        out = stock.screen(custom=True, filters=None)
        self.assertIn("error", out)

    def test_screen_bad_filter_expression(self):
        out = stock.screen(custom=True, filters=["marketcap big"])  # missing value
        self.assertIn("error", out)
        self.assertIn("FIELD OP VALUE", out["error"])

    def test_screen_fields_lists_categories(self):
        # valid_fields is a static yfinance table — no network needed.
        out = stock.screen(fields=True, qtype="equity")
        self.assertIn("valid_fields", out)
        self.assertIn("eq_fields", out["valid_fields"])

    def test_market_unknown_region(self):
        out = stock.market("MARS")
        self.assertIn("error", out)
        self.assertIn("US", out["valid_regions"])

    def test_fred_list_is_static(self):
        out = stock.fred(list_series=True)
        self.assertIn("10y", out["series"])
        self.assertEqual(out["series"]["10y"]["id"], "DGS10")

    def test_fred_unknown_name(self):
        out = stock.fred(name="not_a_series")
        self.assertIn("error", out)
        self.assertIn("available", out)

    def test_fred_no_argument(self):
        out = stock.fred()
        self.assertIn("error", out)

    def test_industry_list_is_offline(self):
        out = stock.industry(list_industries=True)
        self.assertEqual(out["count"], 145)
        self.assertIn("industrials", out["industries_by_sector"])

    def test_industry_unknown_suggests(self):
        out = stock.industry("ai")
        self.assertIn("error", out)
        self.assertIn("hint", out)

    def test_industry_no_argument(self):
        out = stock.industry()
        self.assertIn("error", out)

    def test_extract_section_skips_toc_stub(self):
        # The heading appears in the TOC and as the real section; the extractor
        # must return the long body, not the one-line TOC entry.
        text = ("TABLE OF CONTENTS  Item 2. Management's Discussion and Analysis 13  "
                "Item 3. Quantitative Disclosures 19\n"
                "Item 2. Management's Discussion and Analysis of Financial Condition "
                + ("revenue grew and margins expanded. " * 60)
                + " Item 3. Quantitative and Qualitative Disclosures")
        seg = stock._extract_section(text, "10-Q", "mda")
        self.assertIsNotNone(seg)
        self.assertIn("margins expanded", seg)
        self.assertNotIn("TABLE OF CONTENTS", seg)

    def test_ttm_balance_sheet_rejected(self):
        # Balance sheets are point-in-time; TTM is meaningless. Returns before
        # any network call.
        out = stock.financials("AAPL", statement="balance", ttm=True)
        self.assertIn("error", out)
        self.assertIn("TTM balance", out["error"])


class TestTableToMarkdown(unittest.TestCase):
    def test_empty_table(self):
        self.assertEqual(stock._table_to_markdown([]), "")

    def test_simple_2x2(self):
        out = stock._table_to_markdown([["Q1", "Q2"], ["100", "120"]])
        self.assertIn("| Q1 | Q2 |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| 100 | 120 |", out)

    def test_none_cells_become_dash(self):
        out = stock._table_to_markdown([["A", None], [None, "B"]])
        self.assertIn("| A | - |", out)
        self.assertIn("| - | B |", out)

    def test_pipe_chars_in_cells_escaped(self):
        out = stock._table_to_markdown([["a|b", "c"]])
        # pipe inside cell must not break columns
        self.assertEqual(out.count("|"), 6)  # 3 separators per row × 2 rows incl header sep


class TestPdfToText(unittest.TestCase):
    def test_normal_pdf_has_page_markers(self):
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        text = stock._pdf_to_text(raw)
        self.assertIn("--- Page 1 ---", text)
        self.assertGreater(len(text), 1000)

    def test_normal_pdf_has_tables_extracted(self):
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        text = stock._pdf_to_text(raw)
        # An investor deck almost certainly has at least one table somewhere
        self.assertIn("[Table ", text)

    def test_corrupt_pdf_raises(self):
        with open(os.path.join(FIXTURES, "corrupt.pdf"), "rb") as f:
            raw = f.read()
        with self.assertRaises(Exception):
            stock._pdf_to_text(raw)

    def test_scanned_pdf_returns_empty_text(self):
        with open(os.path.join(FIXTURES, "scanned.pdf"), "rb") as f:
            raw = f.read()
        text = stock._pdf_to_text(raw)
        # Just page marker, no actual extracted text
        self.assertIn("--- Page 1 ---", text)
        # Strip page markers and whitespace; what's left should be empty
        import re
        body = re.sub(r"--- Page \d+ ---", "", text).strip()
        self.assertEqual(body, "")


class TestFetchDocText(unittest.TestCase):
    def test_html_url_routes_to_html_parser(self):
        # Monkeypatch _edgar_get_html and _html_to_text via the module
        orig_html = stock._edgar_get_html
        orig_to_text = stock._html_to_text
        stock._edgar_get_html = lambda url: b"<html><body>hello world</body></html>"
        stock._html_to_text = lambda raw: "hello world" if b"hello" in raw else ""
        try:
            text, ctype = stock._fetch_doc_text("https://www.sec.gov/foo/bar.htm")
            self.assertEqual(text, "hello world")
            self.assertEqual(ctype, "html")
        finally:
            stock._edgar_get_html = orig_html
            stock._html_to_text = orig_to_text

    def test_pdf_url_routes_to_pdf_parser(self):
        with open(os.path.join(FIXTURES, "sym_8k_ex992_2026-05-06.pdf"), "rb") as f:
            raw = f.read()
        orig = stock._edgar_get_html
        stock._edgar_get_html = lambda url: raw  # serves bytes for any url
        try:
            text, ctype = stock._fetch_doc_text("https://www.sec.gov/foo/bar.pdf")
            self.assertEqual(ctype, "pdf")
            self.assertIn("--- Page 1 ---", text)
        finally:
            stock._edgar_get_html = orig

    def test_unsupported_extension_raises(self):
        with self.assertRaises(ValueError) as cm:
            stock._fetch_doc_text("https://www.sec.gov/foo/bar.xlsx")
        self.assertIn("unsupported", str(cm.exception).lower())


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
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            result = stock._edgar_list_filings("0001069258", types=["8-K"])
            self.assertTrue(all(r["type"] == "8-K" for r in result))
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached

    def test_date_range_filter(self):
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
        orig_get, orig_cached = self._stub_submissions(stock)
        try:
            r1 = stock._edgar_list_filings("0001069258", types=["8-k"])
            r2 = stock._edgar_list_filings("0001069258", types=["8-K"])
            self.assertEqual(len(r1), len(r2))
        finally:
            stock._edgar_get = orig_get
            stock._cached = orig_cached


class TestEdgarFilingExhibits(unittest.TestCase):
    def test_parses_exhibit_map_from_index_json(self):
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


class TestSecFilingsExtended(unittest.TestCase):
    def setUp(self):
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
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._cached = self._orig_cached

    def test_backwards_compat_limit_only(self):
        r = stock.sec_filings("KTOS", limit=10)
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
        r = stock.sec_filings("KTOS", from_date="2026-02-01", to_date="2026-03-31")
        for f in r["filings"]:
            self.assertGreaterEqual(f["date"], "2026-02-01")
            self.assertLessEqual(f["date"], "2026-03-31")
        self.assertEqual(r["filter"]["from"], "2026-02-01")
        self.assertEqual(r["filter"]["to"], "2026-03-31")

    def test_type_filter(self):
        r = stock.sec_filings("KTOS", types=["8-K"])
        for f in r["filings"]:
            self.assertEqual(f["type"], "8-K")

    def test_no_match_returns_empty_not_error(self):
        r = stock.sec_filings("KTOS", from_date="1990-01-01", to_date="1990-12-31")
        self.assertEqual(r["filings"], [])
        self.assertNotIn("error", r)


def _run_cli(*args):
    out = subprocess.run([PYTHON, SCRIPT, *args],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout


def _skip_if_unavailable(payload):
    """Skip live tests when an upstream is rate-limiting, slow, or unreachable."""
    err = (payload.get("error", "") if isinstance(payload, dict) else "").lower()
    for marker in ("rate limited", "failed", "connection", "timed out",
                   "timeout", "urlopen", "429", "503"):
        if marker in err:
            raise unittest.SkipTest(f"live data unavailable: {err}")


class LiveCliSmokeTests(unittest.TestCase):
    """Exercise CLI wiring + JSON output end-to-end. Tolerant of no network."""

    def test_quote_outputs_valid_json(self):
        code, out = _run_cli("quote", "AAPL", "--no-cache")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["ticker"], "AAPL")
        self.assertIn("price", payload)

    def test_info_outputs_valid_json(self):
        code, out = _run_cli("info", "AAPL", "--no-cache")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["ticker"], "AAPL")

    def test_search_resolves_name(self):
        code, out = _run_cli("search", "apple")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        symbols = [r["symbol"] for r in payload["results"]]
        self.assertIn("AAPL", symbols)

    def test_sector_valid(self):
        code, out = _run_cli("sector", "technology")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["sector"], "Technology")

    def test_financials_ttm(self):
        code, out = _run_cli("financials", "AAPL", "--ttm", "--no-cache")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["period_type"], "ttm")
        self.assertTrue(payload["data"])

    def test_sec_filings(self):
        code, out = _run_cli("sec-filings", "AAPL", "--limit", "3")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertTrue(payload["filings"])
        self.assertIn("type", payload["filings"][0])

    def test_shares(self):
        code, out = _run_cli("shares", "AAPL")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertIn("shares_outstanding", payload)
        self.assertTrue(payload["history"])

    def test_valuation(self):
        code, out = _run_cli("valuation", "AAPL")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertTrue(payload["periods"])
        self.assertIn("Trailing P/E", payload["data"])

    def test_edgar_headline(self):
        code, out = _run_cli("edgar", "AAPL")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["cik"], "0000320193")
        self.assertIn("revenue", payload["financials"])

    def test_filing_text_mda(self):
        code, out = _run_cli("filing-text", "AAPL")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["form"], "10-Q")
        self.assertGreater(payload["char_count"], 500)
        self.assertTrue(payload["text"])

    def test_news_historical_gdelt(self):
        code, out = _run_cli("news", "INTC", "--from", "2024-08-01",
                             "--to", "2024-08-10", "--limit", "3")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)  # GDELT 429s skip rather than fail
        self.assertIn("GDELT", payload["source"])
        self.assertIn("news", payload)

    def test_industry_live(self):
        code, out = _run_cli("industry", "aerospace-defense")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["sector"], "Industrials")
        self.assertTrue(payload["top_companies"])

    def test_fred_live(self):
        code, out = _run_cli("fred", "10y")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["series_id"], "DGS10")
        self.assertIn("value", payload["latest"])

    def test_screen_custom(self):
        code, out = _run_cli("screen", "--custom", "--filter", "region eq us",
                             "--filter", "intradaymarketcap gt 100000000000", "--limit", "3")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["mode"], "custom")
        self.assertIn("results", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
