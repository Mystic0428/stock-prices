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


class Form4CodeParserTests(unittest.TestCase):
    """Maps yfinance's Text column into SEC Form 4 codes. The headline
    `summary_6mo_yfinance` lumps grants and tax-withholdings into
    purchases/sales — splitting by code is what makes `is management
    buying?` answerable correctly."""

    def test_sale(self):
        self.assertEqual(stock._parse_form4_code("Sale at price 290.00 per share."), "S")

    def test_sale_with_range(self):
        self.assertEqual(stock._parse_form4_code("Sale at price 284.57 - 285.04 per share."), "S")

    def test_purchase(self):
        self.assertEqual(stock._parse_form4_code("Purchase at price 12.50 per share."), "P")

    def test_rsu_grant(self):
        # FLY pattern: insiders all received Stock Award(Grant) @ $0.00 — must NOT be P.
        self.assertEqual(stock._parse_form4_code("Stock Award(Grant) at price 0.00 per share."), "A")

    def test_option_exercise_conversion(self):
        # FLY pattern: paired M+S where Wheeler converted derivatives @ $2.31 then
        # sold @ $45 — the conversion leg must be M, not P.
        self.assertEqual(
            stock._parse_form4_code("Conversion of Exercise of derivative security at price 2.31 per share."),
            "M",
        )

    def test_gift(self):
        self.assertEqual(stock._parse_form4_code("Stock Gift at price 0.00 per share."), "G")

    def test_tax_withholding(self):
        self.assertEqual(stock._parse_form4_code("Tax Withholding upon vesting of RSU."), "F")

    def test_unknown_returns_none(self):
        self.assertIsNone(stock._parse_form4_code(""))
        self.assertIsNone(stock._parse_form4_code(None))

    def test_grant_beats_purchase_keyword(self):
        # "Restricted Stock Unit award" should NOT match "purchase" even if either
        # word coincidentally appears; grants/awards take precedence.
        self.assertEqual(stock._parse_form4_code("Restricted stock unit award"), "A")


class InfoUnitsTests(unittest.TestCase):
    """`_units` block must be present and document the high-mis-read fields."""

    def test_units_table_loaded(self):
        # _INFO_UNITS is the module-level constant returned in info() output.
        units = stock._INFO_UNITS
        for must_have in ("revenue_growth", "earnings_growth", "free_cashflow",
                          "profit_margin", "total_cash", "forward_pe", "dividend_yield"):
            self.assertIn(must_have, units, f"_units missing key: {must_have}")

    def test_revenue_growth_marked_as_quarterly_yoy(self):
        # The single most common mis-read — must explicitly call out the quarterly
        # scope so callers don't claim FY annual growth from this number.
        self.assertIn("QUARTER", stock._INFO_UNITS["revenue_growth"].upper())
        self.assertIn("YOY", stock._INFO_UNITS["revenue_growth"].upper())

    def test_dividend_yield_marked_as_percentage(self):
        # Defensive cross-reference with the existing memory: dividendYield
        # is already a percent in yfinance 1.2.0+, don't multiply by 100.
        self.assertIn("percent", stock._INFO_UNITS["dividend_yield"].lower())


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
        seg = stock._extract_section_10k(text, "10-Q", "mda")
        self.assertIsNotNone(seg)
        self.assertIn("margins expanded", seg)
        self.assertNotIn("TABLE OF CONTENTS", seg)

    def test_extract_section_loose_head_fallback(self):
        # Filer renders the body heading as the bare title (no "Item 1A."
        # prefix). The precise anchor misses; the loose title-only head catches.
        text = ("TABLE OF CONTENTS  Risk Factors 12  Properties 30\n"
                "Risk Factors\n"
                + ("our business faces material risks and uncertainties. " * 60)
                + " Item 2. Properties")
        # Precise "Item 1A. Risk Factors" anchor is absent here...
        self.assertNotIn("Item 1A", text)
        seg = stock._extract_section_10k(text, "10-K", "risk")
        self.assertIsNotNone(seg)
        self.assertIn("material risks", seg)
        self.assertNotIn("TABLE OF CONTENTS", seg)

    def test_extract_section_20f_all_sections(self):
        # Synthetic 20-F with FPI item numbering. Each section's body must be
        # extracted via its Item anchor and bounded by the next item.
        text = (
            "Item 3. Key Information\n"
            "D. Risk Factors\n" + ("currency and regulatory risks abound. " * 40) +
            "Item 4. Information on the Company\n" + ("we build software for teams. " * 40) +
            "Item 4A. Unresolved Staff Comments\nNone.\n"
            "Item 5. Operating and Financial Review and Prospects\n"
            + ("revenue rose and operating margin improved. " * 40) +
            "Item 6. Directors, Senior Management and Employees\n"
            + ("our board comprises seven members. " * 40) +
            "Item 7. Major Shareholders and Related Party Transactions\nfoo"
        )
        risk = stock._extract_section_20f(text, "risk")
        self.assertIsNotNone(risk)
        self.assertIn("currency and regulatory risks", risk)
        self.assertNotIn("we build software", risk)  # bounded at Item 4

        business = stock._extract_section_20f(text, "business")
        self.assertIsNotNone(business)
        self.assertIn("we build software", business)

        mda = stock._extract_section_20f(text, "mda")
        self.assertIsNotNone(mda)
        self.assertIn("operating margin improved", mda)
        self.assertNotIn("our board comprises", mda)  # bounded at Item 6

        directors = stock._extract_section_20f(text, "directors")
        self.assertIsNotNone(directors)
        self.assertIn("our board comprises", directors)

    def test_extract_section_20f_unknown_section(self):
        self.assertIsNone(stock._extract_section_20f("Item 4. Information", "properties"))

    def test_ttm_balance_sheet_rejected(self):
        # Balance sheets are point-in-time; TTM is meaningless. Returns before
        # any network call.
        out = stock.financials("AAPL", statement="balance", ttm=True)
        self.assertIn("error", out)
        self.assertIn("TTM balance", out["error"])


# Trimmed real Form 144 (MNDY, Eran Zinman, 2025-12-09) — modern eXML schema.
FORM144_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/ownership" xmlns:com="http://www.sec.gov/edgar/common">
  <headerData><submissionType>144</submissionType></headerData>
  <formData>
    <issuerInfo>
      <issuerCik>0001845338</issuerCik>
      <issuerName>monday.com Ltd.</issuerName>
      <nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold>Zinman Eran</nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold>
      <relationshipsToIssuer>
        <relationshipToIssuer>Officer</relationshipToIssuer>
        <relationshipToIssuer>Director</relationshipToIssuer>
      </relationshipsToIssuer>
    </issuerInfo>
    <securitiesInformation>
      <securitiesClassTitle>Ordinary</securitiesClassTitle>
      <brokerOrMarketmakerDetails><name>Oppenheimer &amp; Co. inc</name></brokerOrMarketmakerDetails>
      <noOfUnitsSold>60000</noOfUnitsSold>
      <aggregateMarketValue>9718200.00</aggregateMarketValue>
      <noOfUnitsOutstanding>50773337</noOfUnitsOutstanding>
      <approxSaleDate>12/09/2025</approxSaleDate>
      <securitiesExchangeName>Nasdaq</securitiesExchangeName>
    </securitiesInformation>
    <securitiesToBeSold>
      <securitiesClassTitle>Ordinary</securitiesClassTitle>
      <acquiredDate>03/27/2016</acquiredDate>
      <natureOfAcquisitionTransaction>Exercised Options</natureOfAcquisitionTransaction>
      <amountOfSecuritiesAcquired>851000</amountOfSecuritiesAcquired>
    </securitiesToBeSold>
    <nothingToReportFlagOnSecuritiesSoldInPast3Months>N</nothingToReportFlagOnSecuritiesSoldInPast3Months>
    <securitiesSoldInPast3Months>
      <sellerDetails><name>Zinman Eran</name></sellerDetails>
      <securitiesClassTitle>Ordinary</securitiesClassTitle>
      <saleDate>09/17/2025</saleDate>
      <amountOfSecuritiesSold>7500</amountOfSecuritiesSold>
      <grossProceeds>1598310.77</grossProceeds>
    </securitiesSoldInPast3Months>
    <securitiesSoldInPast3Months>
      <sellerDetails><name>Zinman Eran</name></sellerDetails>
      <securitiesClassTitle>Ordinary</securitiesClassTitle>
      <saleDate>10/01/2025</saleDate>
      <amountOfSecuritiesSold>7500</amountOfSecuritiesSold>
      <grossProceeds>1442949.27</grossProceeds>
    </securitiesSoldInPast3Months>
    <securitiesSoldInPast3Months>
      <sellerDetails><name>Zinman Eran</name></sellerDetails>
      <securitiesClassTitle>Ordinary</securitiesClassTitle>
      <saleDate>11/03/2025</saleDate>
      <amountOfSecuritiesSold>7652</amountOfSecuritiesSold>
      <grossProceeds>1578626.62</grossProceeds>
    </securitiesSoldInPast3Months>
    <remarks>Under a 10b5-1 sale plan adopted on 09/08/2025</remarks>
    <noticeSignature>
      <noticeDate>12/09/2025</noticeDate>
      <planAdoptionDates><planAdoptionDate>09/08/2025</planAdoptionDate></planAdoptionDates>
      <signature>Zinman Eran</signature>
    </noticeSignature>
  </formData>
</edgarSubmission>"""


class Form144ParserTests(unittest.TestCase):
    def setUp(self):
        self.d = stock._parse_form144_xml(FORM144_XML)

    def test_issuer_and_filer(self):
        self.assertEqual(self.d["issuer"], "monday.com Ltd.")
        self.assertEqual(self.d["filer"], "Zinman Eran")
        self.assertEqual(self.d["relationship"], ["Officer", "Director"])

    def test_proposed_sale(self):
        ps = self.d["proposed_sale"]
        self.assertEqual(ps["shares"], 60000)
        self.assertEqual(ps["aggregate_market_value"], 9718200.0)
        self.assertEqual(ps["shares_outstanding"], 50773337)
        self.assertEqual(ps["approx_sale_date"], "12/09/2025")
        self.assertEqual(ps["exchange"], "Nasdaq")
        self.assertIn("Oppenheimer", ps["broker"])
        self.assertEqual(ps["class"], "Ordinary")

    def test_sold_past_3_months(self):
        sales = self.d["sold_past_3_months"]
        self.assertEqual(len(sales), 3)
        self.assertEqual(sales[0]["shares"], 7500)
        self.assertEqual(sales[0]["gross_proceeds"], 1598310.77)
        self.assertEqual(sales[0]["sale_date"], "09/17/2025")
        # Totals across the window
        self.assertEqual(self.d["total_sold_past_3_months_shares"], 22652)
        self.assertAlmostEqual(self.d["total_sold_past_3_months_proceeds"], 4619886.66, places=2)

    def test_plan_and_remarks(self):
        self.assertIn("10b5-1", self.d["remarks"])
        self.assertEqual(self.d["plan_adoption_dates"], ["09/08/2025"])
        self.assertEqual(self.d["notice_date"], "12/09/2025")

    def test_nothing_to_report_flag(self):
        xml = FORM144_XML.replace(
            "<nothingToReportFlagOnSecuritiesSoldInPast3Months>N",
            "<nothingToReportFlagOnSecuritiesSoldInPast3Months>Y")
        d = stock._parse_form144_xml(xml)
        self.assertEqual(d["nothing_to_report_past_3_months"], True)


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


class TestFilingTextRegression(unittest.TestCase):
    """Behavior of filing-text 10-K/10-Q must NOT change in refactor."""

    def setUp(self):
        self._orig_cik = stock._edgar_cik
        self._orig_get = stock._edgar_get
        self._orig_get_html = stock._edgar_get_html
        self._orig_cached = stock._cached
        stock._cached = lambda key, ttl, fn: fn()

    def tearDown(self):
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._edgar_get_html = self._orig_get_html
        stock._cached = self._orig_cached

    def test_10q_mda_default_unchanged(self):
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


class TestFilingText8K(unittest.TestCase):
    def setUp(self):
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
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._edgar_get_html = self._orig_get_html
        stock._cached = self._orig_cached

    def test_8k_body_default(self):
        r = stock.filing_text("SYM", form_type="8-K")
        self.assertEqual(r["form"], "8-K")
        self.assertEqual(r["filing_date"], "2026-05-06")
        # Defaults to FIRST of the two same-day → actually should be ambiguity error
        # See test_date_multiple_same_day_errors below; this test only when --date NOT given
        self.assertIn("text", r)

    def test_date_multiple_same_day_errors(self):
        r = stock.filing_text("SYM", form_type="8-K", date="2026-05-06")
        self.assertIn("error", r)
        self.assertIn("multiple", r["error"].lower())
        self.assertIn("0001-26-1", r["error"])
        self.assertIn("0001-26-2", r["error"])

    def test_date_missing_lists_nearest(self):
        r = stock.filing_text("SYM", form_type="8-K", date="2026-04-15")
        self.assertIn("error", r)
        self.assertIn("nearest", r["error"].lower())
        self.assertIn("2026-04-01", r["error"])
        self.assertIn("2026-05-06", r["error"])

    def test_accession_overrides_type(self):
        r = stock.filing_text("SYM", accession="0001-26-3")
        self.assertEqual(r["accession"], "0001-26-3")
        self.assertEqual(r["filing_date"], "2026-04-01")

    def test_accession_not_found(self):
        r = stock.filing_text("SYM", accession="9999-99-9")
        self.assertIn("error", r)
        self.assertIn("not found", r["error"].lower())

    def test_exhibit_html_fetches_press_release(self):
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.1")
        self.assertEqual(r["content_type"], "html")
        self.assertGreater(r["char_count"], 0)
        self.assertIn("EX-99.1", r["section"])

    def test_exhibit_pdf_fetches_investor_deck(self):
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.2")
        self.assertEqual(r["content_type"], "pdf")
        self.assertIn("--- Page 1 ---", r["text"])
        self.assertIn("EX-99.2", r["section"])

    def test_exhibit_case_insensitive(self):
        r1 = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.1")
        r2 = stock.filing_text("SYM", accession="0001-26-1", exhibit="EX-99.1")
        r3 = stock.filing_text("SYM", accession="0001-26-1", exhibit="99.1")
        self.assertEqual(r1["char_count"], r2["char_count"])
        self.assertEqual(r1["char_count"], r3["char_count"])

    def test_exhibit_not_found_lists_available(self):
        r = stock.filing_text("SYM", accession="0001-26-1", exhibit="ex-99.9")
        self.assertIn("error", r)
        self.assertIn("EX-99.1", r["error"])
        self.assertIn("EX-99.2", r["error"])

    def test_exhibit_and_section_mutex(self):
        r = stock.filing_text("SYM", accession="0001-26-1",
                              exhibit="ex-99.1", section="mda")
        self.assertIn("error", r)
        self.assertIn("mutex", r["error"].lower() + " " + "mutually exclusive".lower())

    def test_list_exhibits_mode(self):
        r = stock.filing_text("SYM", accession="0001-26-1", list_exhibits=True)
        self.assertIn("exhibits", r)
        self.assertIn("EX-99.1", r["exhibits"])
        self.assertIn("EX-99.2", r["exhibits"])
        self.assertNotIn("text", r)  # list-exhibits doesn't fetch content

    def test_8k_section_label_is_full_document(self):
        """8-K body output must have section='full document', not 'mda' (Fix 3)."""
        r = stock.filing_text("SYM", form_type="8-K", accession="0001-26-3")
        self.assertNotIn("error", r)
        self.assertEqual(r.get("section"), "full document")

    def test_cli_section_mda_with_exhibit_triggers_mutex(self):
        """CLI: explicit --section mda --exhibit ex-99.1 must error (Fix 1)."""
        code, out = _run_cli("filing-text", "SYM", "--section", "mda",
                             "--exhibit", "ex-99.1")
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("mutex", payload["error"].lower() + " mutually exclusive")


class TestFilingText20F6KFallback(unittest.TestCase):
    """20-F sections, 6-K full-doc, and graceful section fallback (P1+P2)."""

    def setUp(self):
        self._orig_cik = stock._edgar_cik
        self._orig_get = stock._edgar_get
        self._orig_get_html = stock._edgar_get_html
        self._orig_cached = stock._cached
        stock._cached = lambda key, ttl, fn: fn()
        stock._edgar_cik = lambda t: ("0001640147", "MONDAY")
        self.sub = {
            "filings": {"recent": {
                "form":            ["20-F", "6-K", "10-K"],
                "filingDate":      ["2026-03-01", "2026-02-15", "2026-03-02"],
                "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
                "primaryDocument": ["f20f.htm", "f6k.htm", "f10k.htm"],
                "primaryDocDescription": ["20-F", "6-K", "10-K"],
                "items":           ["", "", ""],
            }}
        }
        stock._edgar_get = lambda url: self.sub if "submissions" in url else None
        self.doc_20f = (
            "<html><body>"
            "Item 3. Key Information D. Risk Factors "
            + ("currency and regulatory risks abound. " * 60) +
            "Item 4. Information on the Company "
            + ("we build work-OS software. " * 60) +
            "Item 5. Operating and Financial Review and Prospects "
            + ("revenue rose sharply. " * 60) +
            "Item 6. Directors, Senior Management and Employees "
            + ("the board has seven members. " * 60) +
            "Item 7. Major Shareholders foo"
            "</body></html>").encode()
        self.doc_6k = ("<html><body>"
                       + ("Monday.com reports record quarterly results. " * 40)
                       + "</body></html>").encode()
        # 10-K body with NO MD&A heading at all -> mda extraction fails entirely
        self.doc_10k = ("<html><body>"
                        "Item 1. Business " + ("we sell software. " * 60) +
                        "Item 1A. Risk Factors " + ("risks exist. " * 60) +
                        "</body></html>").encode()

        def fake_html(url):
            if "f20f" in url:
                return self.doc_20f
            if "f6k" in url:
                return self.doc_6k
            if "f10k" in url:
                return self.doc_10k
            return b""
        stock._edgar_get_html = fake_html

    def tearDown(self):
        stock._edgar_cik = self._orig_cik
        stock._edgar_get = self._orig_get
        stock._edgar_get_html = self._orig_get_html
        stock._cached = self._orig_cached

    def test_20f_default_section_is_mda(self):
        r = stock.filing_text("MNDY", form_type="20-F")
        self.assertNotIn("error", r)
        self.assertEqual(r["form"], "20-F")
        self.assertIn("revenue rose sharply", r["text"])

    def test_20f_risk_bounded_at_item4(self):
        r = stock.filing_text("MNDY", form_type="20-F", section="risk")
        self.assertNotIn("error", r)
        self.assertIn("regulatory risks", r["text"])
        self.assertNotIn("we build work-OS", r["text"])

    def test_20f_directors_section(self):
        r = stock.filing_text("MNDY", form_type="20-F", section="directors")
        self.assertNotIn("error", r)
        self.assertIn("board has seven members", r["text"])

    def test_6k_returns_full_doc_not_error(self):
        r = stock.filing_text("MNDY", form_type="6-K")
        self.assertNotIn("error", r)
        self.assertEqual(r["form"], "6-K")
        self.assertEqual(r["section"], "full document")
        self.assertIn("record quarterly results", r["text"])

    def test_section_fallback_full_when_anchors_missing(self):
        # mda is valid for 10-K but this doc has no MD&A heading -> full-doc fallback
        r = stock.filing_text("MNDY", form_type="10-K", section="mda")
        self.assertNotIn("error", r)
        self.assertEqual(r.get("section_extraction"), "fallback_full")
        self.assertIn("we sell software", r["text"])  # whole doc returned

    def test_near_full_extraction_triggers_fallback(self):
        # A "section" that spans ~the whole doc means anchors over-matched
        # (title-only 20-F TOC trap) -> guard converts it to full-doc fallback.
        self.sub["filings"]["recent"]["form"][2] = "20-F"
        self.doc_10k = ("<html><body>x "
                        "Risk Factors " + ("recurring risk text. " * 800) +
                        "Item 4. Information on the Company tail"
                        "</body></html>").encode()
        r = stock.filing_text("MNDY", form_type="20-F", section="risk")
        self.assertNotIn("error", r)
        self.assertEqual(r.get("section_extraction"), "fallback_full")

    def test_invalid_section_name_still_errors(self):
        # 'bogus' is not a valid 10-K section -> keep the helpful hint error
        r = stock.filing_text("MNDY", form_type="10-K", section="bogus")
        self.assertIn("error", r)
        self.assertIn("valid sections", r["error"])


class TestNormalizeExhibit(unittest.TestCase):
    """Unit tests for _normalize_exhibit (Fix 2)."""

    def test_canonical_form_unchanged(self):
        self.assertEqual(stock._normalize_exhibit("EX-99.1"), "EX-99.1")

    def test_lowercase_normalized(self):
        self.assertEqual(stock._normalize_exhibit("ex-99.1"), "EX-99.1")

    def test_no_dash_prefix(self):
        """EX99.1 (no dash) must not produce EX-EX99.1."""
        self.assertEqual(stock._normalize_exhibit("EX99.1"), "EX-99.1")

    def test_number_only(self):
        self.assertEqual(stock._normalize_exhibit("99.1"), "EX-99.1")

    def test_lowercase_no_dash(self):
        self.assertEqual(stock._normalize_exhibit("ex99.1"), "EX-99.1")

    def test_ex_underscore_prefix(self):
        self.assertEqual(stock._normalize_exhibit("EX_99.1"), "EX-99.1")


class TestExtractSectionProspectus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "ambq_s1_2024.htm"), "rb") as f:
            cls.s1_text = stock._html_to_text(f.read())
        with open(os.path.join(FIXTURES, "ktos_s3asr_2026-02.htm"), "rb") as f:
            cls.s3_text = stock._html_to_text(f.read())

    def test_risk_factors_extracted(self):
        out = stock._extract_section_prospectus(self.s1_text, "risk")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 5000)
        self.assertIn("risk", out.lower())

    def test_use_of_proceeds_extracted(self):
        out = stock._extract_section_prospectus(self.s1_text, "use-of-proceeds")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 100)

    def test_dilution_extracted(self):
        out = stock._extract_section_prospectus(self.s1_text, "dilution")
        self.assertIsNotNone(out)
        # Dilution section almost always has a $ sign and "per share"
        self.assertIn("$", out)

    def test_underwriting_extracted_in_s3asr(self):
        out = stock._extract_section_prospectus(self.s3_text, "underwriting")
        # S-3ASR may or may not have underwriting itself (often in 424B5);
        # so just assert: result is either None or a real string > 200 chars.
        if out is not None:
            self.assertGreater(len(out), 200)

    def test_invalid_section_returns_none(self):
        out = stock._extract_section_prospectus(self.s1_text, "not-a-real-section")
        self.assertIsNone(out)


class TestExtractSectionDef14a(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "def14a_sample.htm"), "rb") as f:
            cls.text = stock._html_to_text(f.read())

    def test_compensation_extracted(self):
        out = stock._extract_section_def14a(self.text, "compensation")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 500)

    def test_directors_extracted(self):
        out = stock._extract_section_def14a(self.text, "directors")
        self.assertIsNotNone(out)

    def test_transactions_extracted(self):
        out = stock._extract_section_def14a(self.text, "transactions")
        # Some DEF 14As don't have related-party section; allow None
        # but if not None, must be non-trivial
        if out is not None:
            self.assertGreater(len(out), 50)

    def test_invalid_section_returns_none(self):
        self.assertIsNone(stock._extract_section_def14a(self.text, "not-real"))


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

    def test_13f_list_aliases_cli(self):
        # Offline: should not need network for --list.
        code, out = _run_cli("13f", "--list")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("aliases", payload)
        self.assertIn("buffett", payload["aliases"])

    def test_13f_buffett_live(self):
        # Network-tolerant smoke: Berkshire's 13F + diff must round-trip,
        # and v2.1 must populate `ticker` via OpenFIGI for visible holdings.
        code, out = _run_cli("13f", "buffett", "--top", "5")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        if "error" in payload:
            self.skipTest(payload["error"])
        self.assertEqual(payload["cik"], "0001067983")
        self.assertIn("latest", payload)
        self.assertGreater(payload["latest"]["position_count"], 0)
        self.assertLessEqual(len(payload["latest"]["holdings"]), 5)
        h0 = payload["latest"]["holdings"][0]
        for k in ("cusip", "name", "shares", "value_usd", "pct_of_portfolio", "ticker"):
            self.assertIn(k, h0)
        names = [h["name"].upper() for h in payload["latest"]["holdings"]]
        self.assertTrue(any("APPLE" in n for n in names),
                        f"Expected APPLE in top-5 names, got: {names}")
        # AAPL's CUSIP must resolve to ticker AAPL (unless OpenFIGI is unreachable)
        aapl_row = next((h for h in payload["latest"]["holdings"]
                         if "APPLE" in h["name"].upper()), None)
        if aapl_row and aapl_row.get("ticker") is None:
            self.skipTest("OpenFIGI unreachable / rate-limited")
        if aapl_row:
            self.assertEqual(aapl_row["ticker"], "AAPL")

    def test_13f_holders_aapl_live(self):
        # Reverse lookup AAPL should find Berkshire holding it. Restrict to
        # one manager to keep the test fast.
        code, out = _run_cli("13f-holders", "AAPL", "--managers", "buffett")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        if "error" in payload:
            self.skipTest(payload["error"])
        self.assertEqual(payload["query"]["ticker"], "AAPL")
        self.assertGreaterEqual(payload["holders_found"], 1)
        first = payload["holders"][0]
        self.assertEqual(first["manager"], "buffett")
        self.assertIn("APPLE", first["name"].upper())
        for k in ("cusip", "shares", "value_usd", "pct_of_portfolio", "change"):
            self.assertIn(k, first)

    def test_screen_custom(self):
        code, out = _run_cli("screen", "--custom", "--filter", "region eq us",
                             "--filter", "intradaymarketcap gt 100000000000", "--limit", "3")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        _skip_if_unavailable(payload)
        self.assertEqual(payload["mode"], "custom")
        self.assertIn("results", payload)


class TestMaxCharsPolicy(unittest.TestCase):
    def test_defaults_per_form_family(self):
        # primary doc, not full
        self.assertEqual(stock._default_max_chars("10-Q", "html", None, False), 150_000)
        self.assertEqual(stock._default_max_chars("10-K", "html", None, False), 150_000)
        self.assertEqual(stock._default_max_chars("10-K", "html", None, True),  500_000)
        self.assertEqual(stock._default_max_chars("10-Q", "html", None, True),  200_000)
        self.assertEqual(stock._default_max_chars("8-K",  "html", None, False), 50_000)
        self.assertEqual(stock._default_max_chars("S-1",  "html", None, False), 250_000)
        self.assertEqual(stock._default_max_chars("S-3ASR", "html", None, True), 500_000)
        self.assertEqual(stock._default_max_chars("424B5", "html", None, False), 250_000)
        self.assertEqual(stock._default_max_chars("DEF 14A", "html", None, False), 200_000)
        # exhibit defaults
        self.assertEqual(stock._default_max_chars("8-K", "html", "EX-99.1", False), 200_000)
        self.assertEqual(stock._default_max_chars("8-K", "pdf",  "EX-99.2", False), 400_000)

    def test_cap_enforced_via_cli(self):
        result = subprocess.run(
            [PYTHON, "scripts/stock.py", "filing-text", "NVDA",
             "--max-chars", "3000000"],
            cwd=REPO,
            capture_output=True, text=True, timeout=30
        )
        # Should print the cap error to stdout and exit normally
        self.assertIn("2,000,000", result.stdout)


class TestLiveSmoke(unittest.TestCase):
    """Network-tolerant tests — skip on connection error, fail on parse error."""

    def _safe_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            self.skipTest(f"network: {e}")

    def test_live_sec_filings_date_range_real(self):
        r = self._safe_call(stock.sec_filings, "KTOS", from_date="2026-01-01",
                            to_date="2026-05-31", types=["8-K"])
        if r is None:
            return  # skipped by _safe_call
        if "error" in r and "rate" in r["error"].lower():
            self.skipTest("EDGAR rate limit")
        self.assertGreater(r["count"], 0)
        # The 2026-03-02 8-K should appear
        dates = [f["date"] for f in r["filings"]]
        self.assertIn("2026-03-02", dates)

    def test_live_filing_text_pdf_real(self):
        # Find SYM's latest 8-K with an EX-99.2 PDF
        listing = self._safe_call(stock.sec_filings, "SYM", types=["8-K"], limit=5)
        if listing is None:
            return  # skipped by _safe_call
        if "error" in listing:
            self.skipTest(listing["error"])
        target_acc = None
        for f in listing["filings"]:
            exs_r = stock.filing_text("SYM", accession=f["accession"], list_exhibits=True)
            if "EX-99.2" in exs_r.get("exhibits", {}) and exs_r["exhibits"]["EX-99.2"].endswith(".pdf"):
                target_acc = f["accession"]
                break
        if not target_acc:
            self.skipTest("no SYM 8-K with PDF EX-99.2 in recent filings")
        r = stock.filing_text("SYM", accession=target_acc, exhibit="ex-99.2")
        self.assertEqual(r["content_type"], "pdf")
        self.assertGreater(r["char_count"], 30_000)

    def test_live_filing_text_s_family_real(self):
        # KTOS S-3ASR may not always exist; try and skip if not
        r = self._safe_call(stock.filing_text, "KTOS", form_type="S-3ASR", full=True)
        if r is None:
            return  # skipped by _safe_call
        if "error" in r and "not found" in r["error"]:
            self.skipTest("KTOS has no S-3ASR currently")
        self.assertIn("text", r)
        self.assertGreater(r["char_count"], 1000)


class TestThirteenfHelpers(unittest.TestCase):
    """Pure unit tests for 13F helpers — no network."""

    def test_resolve_alias(self):
        cik, alias = stock._13f_resolve_manager("buffett")
        self.assertEqual(cik, "0001067983")
        self.assertEqual(alias, "buffett")

    def test_resolve_alias_case_insensitive(self):
        cik, _ = stock._13f_resolve_manager("BUFFETT")
        self.assertEqual(cik, "0001067983")

    def test_resolve_raw_cik(self):
        cik, alias = stock._13f_resolve_manager("1067983")
        self.assertEqual(cik, "0001067983")
        self.assertIsNone(alias)  # No alias when raw CIK passed

    def test_resolve_unknown_returns_none(self):
        cik, _ = stock._13f_resolve_manager("notarealfundmanager")
        self.assertIsNone(cik)

    def test_resolve_empty(self):
        cik, _ = stock._13f_resolve_manager("")
        self.assertIsNone(cik)

    def test_period_to_quarter_edgar_format(self):
        q, end = stock._13f_period_to_quarter("03-31-2026")
        self.assertEqual(q, "2026Q1")
        self.assertEqual(end, "2026-03-31")

    def test_period_to_quarter_iso_format(self):
        q, end = stock._13f_period_to_quarter("2025-12-31")
        self.assertEqual(q, "2025Q4")
        self.assertEqual(end, "2025-12-31")

    def test_period_to_quarter_q2_q3(self):
        self.assertEqual(stock._13f_period_to_quarter("06-30-2025")[0], "2025Q2")
        self.assertEqual(stock._13f_period_to_quarter("09-30-2024")[0], "2024Q3")

    def test_period_off_quarter_returns_none_quarter(self):
        q, end = stock._13f_period_to_quarter("01-15-2026")
        self.assertIsNone(q)  # Not on a quarter-end
        self.assertEqual(end, "2026-01-15")

    def test_period_empty(self):
        self.assertEqual(stock._13f_period_to_quarter(""), (None, None))
        self.assertEqual(stock._13f_period_to_quarter(None), (None, None))

    def test_aliases_have_all_20(self):
        # 20 aliases shipped. Pabrai removed (defunct since 2012); Coatue/Laffont
        # added as active replacement.
        self.assertGreaterEqual(len(stock.MANAGER_ALIASES), 20)
        for must in ("buffett", "ackman", "burry", "klarman", "einhorn",
                     "tepper", "loeb", "druckenmiller", "soros", "dalio",
                     "greenblatt", "miller", "icahn", "laffont", "watsa",
                     "marks", "gates", "simons", "griffin", "cohen"):
            self.assertIn(must, stock.MANAGER_ALIASES, f"missing alias: {must}")


class TestThirteenfParseHoldings(unittest.TestCase):
    """Parser against a synthetic infotable.xml mirroring Berkshire's structure
    where one CUSIP appears across multiple sub-manager rows."""

    SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>50000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>500000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority><Sole>500000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>20000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>200000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority><Sole>200000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>MICROSOFT CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>594918104</cusip>
    <value>100000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>250000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority><Sole>250000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
</informationTable>"""

    def test_aggregates_repeated_cusips(self):
        h = stock._13f_parse_holdings(self.SAMPLE)
        self.assertEqual(len(h), 2)  # 2 unique CUSIPs (Apple deduped from 2 rows)
        apple = next(x for x in h if x["cusip"] == "037833100")
        self.assertEqual(apple["shares"], 700000)         # 500k + 200k
        self.assertEqual(apple["value_usd"], 70_000_000)  # 50M + 20M

    def test_sorted_by_value_desc(self):
        h = stock._13f_parse_holdings(self.SAMPLE)
        # MSFT 100M > AAPL 70M, so MSFT first
        self.assertEqual(h[0]["cusip"], "594918104")
        self.assertEqual(h[1]["cusip"], "037833100")

    def test_carries_name_and_class(self):
        h = stock._13f_parse_holdings(self.SAMPLE)
        msft = next(x for x in h if x["cusip"] == "594918104")
        self.assertEqual(msft["name"], "MICROSOFT CORP")
        self.assertEqual(msft["title_of_class"], "COM")
        self.assertEqual(msft["share_type"], "SH")

    def test_accepts_bytes_input(self):
        h = stock._13f_parse_holdings(self.SAMPLE.encode())
        self.assertEqual(len(h), 2)


class TestThirteenfDiff(unittest.TestCase):
    def _h(self, cusip, name, shares, value=0):
        return {"cusip": cusip, "name": name, "shares": shares, "value_usd": value}

    def test_classifies_new_added_reduced_sold(self):
        prior = [
            self._h("A", "Apple", 1000, 100),
            self._h("B", "Microsoft", 500, 50),
            self._h("C", "Coca-Cola", 200, 20),  # will be SOLD
        ]
        latest = [
            self._h("A", "Apple", 1500, 150),  # ADDED (+500)
            self._h("B", "Microsoft", 300, 30),  # REDUCED (-200)
            self._h("D", "Nvidia", 100, 80),  # NEW
        ]
        d = stock._13f_diff(latest, prior)
        self.assertEqual([x["cusip"] for x in d["new"]], ["D"])
        self.assertEqual([x["cusip"] for x in d["added"]], ["A"])
        self.assertEqual([x["cusip"] for x in d["reduced"]], ["B"])
        self.assertEqual([x["cusip"] for x in d["sold"]], ["C"])
        # Verify pct_change math
        self.assertEqual(d["added"][0]["pct_change"], 50.0)  # +500/1000
        self.assertEqual(d["reduced"][0]["pct_change"], -40.0)  # -200/500

    def test_unchanged_shares_omitted(self):
        # Same shares but different value (price drift) is NOT a position change.
        prior = [self._h("A", "Apple", 1000, 100)]
        latest = [self._h("A", "Apple", 1000, 150)]
        d = stock._13f_diff(latest, prior)
        self.assertEqual(d["new"], [])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["reduced"], [])
        self.assertEqual(d["sold"], [])

    def test_empty_prior_all_new(self):
        latest = [self._h("A", "Apple", 100, 10), self._h("B", "MSFT", 50, 5)]
        d = stock._13f_diff(latest, [])
        self.assertEqual(len(d["new"]), 2)
        self.assertEqual(d["sold"], [])


class TestThirteenfListAliases(unittest.TestCase):
    def test_list_does_not_hit_network(self):
        # If we accidentally make a network call here, the test would hang or
        # fail without internet. The function must be fully offline.
        out = stock.thirteenf(None, list_aliases=True)
        self.assertIn("aliases", out)
        self.assertGreaterEqual(out["count"], 20)
        for must in ("buffett", "ackman", "burry"):
            self.assertIn(must, out["aliases"])
            self.assertEqual(len(out["aliases"][must]["cik"]), 10)  # 10-digit padded

    def test_no_manager_no_list_errors(self):
        out = stock.thirteenf(None)
        self.assertIn("error", out)


class TestThirteenfResolveError(unittest.TestCase):
    def test_unknown_manager_actionable_error(self):
        out = stock.thirteenf("not-a-real-fund")
        self.assertIn("error", out)
        self.assertIn("13f --list", out["error"])


class TestNormalizeIssuer(unittest.TestCase):
    """Cross-source name normalization: OpenFIGI 'ALPHABET INC-CL A' must
    collapse to the same base name as 13F's 'ALPHABET INC'."""

    def test_strips_class_a_suffix(self):
        self.assertEqual(stock._normalize_issuer("ALPHABET INC-CL A"), "ALPHABET INC")

    def test_strips_class_c_suffix(self):
        self.assertEqual(stock._normalize_issuer("ALPHABET INC-CL C"), "ALPHABET INC")

    def test_strips_class_b_brk(self):
        self.assertEqual(stock._normalize_issuer("BERKSHIRE HATHAWAY INC-CL B"),
                         "BERKSHIRE HATHAWAY INC")

    def test_no_class_suffix_unchanged(self):
        self.assertEqual(stock._normalize_issuer("APPLE INC"), "APPLE INC")

    def test_collapses_punctuation(self):
        self.assertEqual(stock._normalize_issuer("Berkshire Hathaway, Inc."),
                         "BERKSHIRE HATHAWAY INC")

    def test_empty(self):
        self.assertEqual(stock._normalize_issuer(""), "")
        self.assertEqual(stock._normalize_issuer(None), "")

    def test_openfigi_alphabet_matches_13f_alphabet(self):
        # The whole point of normalization: same output from both sources.
        self.assertEqual(stock._normalize_issuer("ALPHABET INC-CL A"),
                         stock._normalize_issuer("ALPHABET INC"))

    def test_corp_corporation_collapse(self):
        # Real bug from smoke test: OpenFIGI says 'NVIDIA CORP', 13F says
        # 'NVIDIA CORPORATION'. Both must normalize to the same string.
        self.assertEqual(stock._normalize_issuer("NVIDIA CORP"),
                         stock._normalize_issuer("NVIDIA CORPORATION"))

    def test_incorporated_collapse(self):
        self.assertEqual(stock._normalize_issuer("FOO INCORPORATED"),
                         stock._normalize_issuer("FOO INC"))

    def test_company_collapse(self):
        self.assertEqual(stock._normalize_issuer("BAR COMPANY"),
                         stock._normalize_issuer("BAR CO"))

    def test_limited_collapse(self):
        self.assertEqual(stock._normalize_issuer("BAZ LIMITED"),
                         stock._normalize_issuer("BAZ LTD"))

    def test_bare_letter_suffix_stripped(self):
        # Real bug: OpenFIGI returns 'MOBILEYE GLOBAL INC-A' (bare -A, no CL)
        # while 13F has 'MOBILEYE GLOBAL INC'. Both must normalize the same.
        self.assertEqual(stock._normalize_issuer("MOBILEYE GLOBAL INC-A"),
                         stock._normalize_issuer("MOBILEYE GLOBAL INC"))

    def test_bare_suffix_does_not_strip_multi_letter(self):
        # Defensive: '-NEW' is a rename indicator, not a class. The single-letter
        # regex must NOT strip it.
        self.assertEqual(stock._normalize_issuer("FOO CORP-NEW"),
                         "FOO CORP-NEW")

    def test_adr_suffix_stripped(self):
        # OpenFIGI tags foreign sponsored ADRs with '-SP ADR' / '-ADR'.
        # 13F just has the base name (often with a different corp suffix).
        self.assertEqual(stock._normalize_issuer("ALIBABA GROUP HOLDING-SP ADR"),
                         stock._normalize_issuer("ALIBABA GROUP HOLDING"))
        self.assertEqual(stock._normalize_issuer("FOO INC-ADR"),
                         stock._normalize_issuer("FOO INC"))
        self.assertEqual(stock._normalize_issuer("FOO INC-SPONSORED ADR"),
                         stock._normalize_issuer("FOO INC"))

    def test_holding_to_hldg_collapse(self):
        # 13F often abbreviates HOLDING(S) -> HLDG(S); both sides must match.
        self.assertEqual(stock._normalize_issuer("ALIBABA GROUP HOLDING"),
                         stock._normalize_issuer("ALIBABA GROUP HLDG"))
        self.assertEqual(stock._normalize_issuer("FAIRFAX FINANCIAL HOLDINGS"),
                         stock._normalize_issuer("FAIRFAX FINANCIAL HLDGS"))

    def test_the_token_stripped(self):
        # OpenFIGI 'WALT DISNEY CO/THE' -> after / becomes space -> trailing THE.
        # 13F: 'WALT DISNEY CO'. After stripping THE both should align (modulo
        # the surname-first quirk, which token-set match handles separately).
        self.assertEqual(stock._normalize_issuer("WALT DISNEY CO/THE"),
                         stock._normalize_issuer("WALT DISNEY CO"))

    def test_ltd_suffix_stripped(self):
        # 13F sometimes keeps trailing LTD that OpenFIGI drops (esp. for ADRs).
        # Real case: ALIBABA GROUP HLDG LTD (13F) vs ALIBABA GROUP HOLDING (OpenFIGI).
        self.assertEqual(stock._normalize_issuer("FOO INC LTD"),
                         stock._normalize_issuer("FOO INC"))
        self.assertEqual(stock._normalize_issuer("BAR PLC"),
                         stock._normalize_issuer("BAR"))

    def test_inc_corp_co_NOT_stripped(self):
        # INC/CORP/CO are nearly always present on both sides — stripping them
        # would risk false positives (e.g. 'APPLE COMPUTER INC' vs 'APPLE INC').
        self.assertIn("INC", stock._normalize_issuer("APPLE INC"))
        self.assertIn("CORP", stock._normalize_issuer("CHEVRON CORPORATION"))
        self.assertIn("CO", stock._normalize_issuer("COCA COLA COMPANY"))


class TestClassFromOpenfigiName(unittest.TestCase):
    def test_class_a(self):
        self.assertEqual(stock._class_from_openfigi_name("ALPHABET INC-CL A"), "A")

    def test_class_c(self):
        self.assertEqual(stock._class_from_openfigi_name("ALPHABET INC-CL C"), "C")

    def test_class_b_brk(self):
        self.assertEqual(stock._class_from_openfigi_name("BERKSHIRE HATHAWAY INC-CL B"), "B")

    def test_no_class(self):
        self.assertIsNone(stock._class_from_openfigi_name("APPLE INC"))

    def test_none_input(self):
        self.assertIsNone(stock._class_from_openfigi_name(None))

    def test_bare_suffix_does_not_extract_class(self):
        # Bare '-A' often indicates a single-class IPO (Mobileye) where
        # class-filtering would exclude valid 13F matches. Only '-CL X' should
        # trigger class extraction (multi-class shares like Alphabet GOOGL/GOOG).
        self.assertIsNone(stock._class_from_openfigi_name("MOBILEYE GLOBAL INC-A"))


class TestOpenFigiMapCusips(unittest.TestCase):
    """Stubs the HTTP layer; verifies batching, caching, and miss handling."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_cache = stock.CACHE_DIR
        stock.CACHE_DIR = os.path.join(self.tmp, "cache")
        os.makedirs(stock.CACHE_DIR, exist_ok=True)
        self._orig_post = stock._openfigi_post
        self.post_calls = []

    def tearDown(self):
        stock.CACHE_DIR = self._orig_cache
        stock._openfigi_post = self._orig_post

    def test_maps_and_caches(self):
        def fake_post(body):
            self.post_calls.append(len(body))
            return [{"data": [{"ticker": "T" + b["idValue"][-3:]}]} for b in body]
        stock._openfigi_post = fake_post

        out = stock._openfigi_map_cusips(["037833100", "594918104"])
        self.assertEqual(out, {"037833100": "T100", "594918104": "T104"})

        # Second call: served from cache, no new POST.
        out2 = stock._openfigi_map_cusips(["037833100", "594918104"])
        self.assertEqual(out2, out)
        self.assertEqual(len(self.post_calls), 1)

    def test_misses_cached_too(self):
        # OpenFIGI returns empty `data` for unknown CUSIPs.
        def fake_post(body):
            self.post_calls.append(len(body))
            return [{"data": []} for _ in body]
        stock._openfigi_post = fake_post

        out = stock._openfigi_map_cusips(["999999999"])
        self.assertEqual(out, {"999999999": None})
        # Second call: cache hit, no refetch.
        stock._openfigi_map_cusips(["999999999"])
        self.assertEqual(len(self.post_calls), 1)

    def test_network_failure_does_not_cache(self):
        def fake_post(body):
            self.post_calls.append(len(body))
            raise ConnectionError("simulated network down")
        stock._openfigi_post = fake_post

        out = stock._openfigi_map_cusips(["037833100"])
        self.assertEqual(out, {"037833100": None})

        # Should retry (no cache) on next call.
        stock._openfigi_map_cusips(["037833100"])
        self.assertEqual(len(self.post_calls), 2)

    def test_batches_respect_keyless_limit(self):
        # Keyless: 10 ids/req. 25 cusips -> 3 batches of 10/10/5.
        # Ensure the env var is NOT set during this test.
        orig_key = os.environ.pop("OPENFIGI_API_KEY", None)
        orig_sleep = stock.time.sleep
        stock.time.sleep = lambda _: None  # skip throttle in tests

        def fake_post(body):
            self.post_calls.append(len(body))
            return [{"data": [{"ticker": "X"}]} for _ in body]
        stock._openfigi_post = fake_post
        try:
            cusips = [f"{i:09d}" for i in range(25)]
            stock._openfigi_map_cusips(cusips)
            self.assertEqual(self.post_calls, [10, 10, 5])
        finally:
            if orig_key is not None:
                os.environ["OPENFIGI_API_KEY"] = orig_key
            stock.time.sleep = orig_sleep

    def test_batches_respect_keyed_limit(self):
        # With key: 100 ids/req. 250 cusips -> 3 batches of 100/100/50.
        os.environ["OPENFIGI_API_KEY"] = "fake-test-key"

        def fake_post(body):
            self.post_calls.append(len(body))
            return [{"data": [{"ticker": "X"}]} for _ in body]
        stock._openfigi_post = fake_post
        try:
            cusips = [f"{i:09d}" for i in range(250)]
            stock._openfigi_map_cusips(cusips)
            self.assertEqual(self.post_calls, [100, 100, 50])
        finally:
            del os.environ["OPENFIGI_API_KEY"]


class TestThirteenfHoldersInput(unittest.TestCase):
    """thirteenf_holders() input handling — no network."""

    def test_no_args_errors(self):
        out = stock.thirteenf_holders()
        self.assertIn("error", out)

    def test_unknown_manager_subset_errors(self):
        out = stock.thirteenf_holders(ticker="AAPL",
                                      managers=["notarealfund", "alsobogus"])
        self.assertIn("error", out)
        self.assertIn("13f --list", out["error"])

    def test_cusip_skips_openfigi_resolver(self):
        # If --cusip is given, OpenFIGI ticker resolution must not run.
        orig = stock._openfigi_resolve_ticker
        stock._openfigi_resolve_ticker = lambda t: (_ for _ in ()).throw(
            AssertionError("ticker resolver must not run when --cusip given"))
        orig_pick = stock._13f_pick_two_periods
        stock._13f_pick_two_periods = lambda cik: (None, None)
        try:
            out = stock.thirteenf_holders(cusip="037833100",
                                          managers=["buffett"])
            self.assertIn("query", out)
            self.assertEqual(out["query"]["cusip"], "037833100")
            self.assertIn("cusip (direct)", out["resolved_via"])
        finally:
            stock._openfigi_resolve_ticker = orig
            stock._13f_pick_two_periods = orig_pick


class TestCusipFrom13G(unittest.TestCase):
    """Stubs the underlying EDGAR fetches; verifies XML-then-HTML extraction
    and caching behaviour."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_cache = stock.CACHE_DIR
        stock.CACHE_DIR = os.path.join(self.tmp, "cache")
        os.makedirs(stock.CACHE_DIR, exist_ok=True)
        self._orig_cik = stock._edgar_cik
        self._orig_list = stock._edgar_list_filings
        self._orig_html = stock._edgar_get_html
        self._orig_h2t = stock._html_to_text
        stock._edgar_cik = lambda t: ("0001045810", "NVIDIA CORP")
        stock._edgar_list_filings = lambda cik, types=None: [
            {"accession": "0000315066-24-002826",
             "date": "2024-11-12", "type": "SC 13G/A",
             "primary_doc_url": "https://www.sec.gov/example/nvda13ga.htm",
             "exhibits_index_url": "https://www.sec.gov/example/",
             "title": "SC 13G/A", "items": [], "primary_doc": "nvda13ga.htm"},
        ]

    def tearDown(self):
        stock.CACHE_DIR = self._orig_cache
        stock._edgar_cik = self._orig_cik
        stock._edgar_list_filings = self._orig_list
        stock._edgar_get_html = self._orig_html
        stock._html_to_text = self._orig_h2t

    def test_xml_structured_path(self):
        # Post-2024 filings: primary_doc.xml contains <issuerCusipNumber>
        # + <issuerName> (used by subject validation)
        def fake_html(url):
            if url.endswith("primary_doc.xml"):
                return (b'<?xml version="1.0"?><x>'
                        b'<issuerName>NVIDIA CORP</issuerName>'
                        b'<issuerCusips><issuerCusipNumber>67066G104</issuerCusipNumber></issuerCusips>'
                        b'</x>')
            return b"<html><body>fallback</body></html>"
        stock._edgar_get_html = fake_html
        out = stock._cusip_from_13g("NVDA")
        self.assertEqual(out, "67066G104")

    def test_xml_rejects_filer_side_filing(self):
        # CIK's submissions index mixes 'this CIK as filer' (e.g. Berkshire
        # filing about Liberty Latin America) with 'this CIK as subject'.
        # We must reject filings where <issuerName> doesn't match the ticker.
        def fake_html(url):
            return (b'<x>'
                    b'<issuerName>LIBERTY LATIN AMERICA LTD</issuerName>'  # WRONG subject
                    b'<issuerCusipNumber>G9001E102</issuerCusipNumber>'    # Liberty's CUSIP
                    b'</x>')
        stock._edgar_get_html = fake_html
        # Stub _edgar_cik to return NVIDIA, then offer a filing whose subject
        # is Liberty Latin America — must NOT return Liberty's CUSIP.
        out = stock._cusip_from_13g("NVDA")
        self.assertIsNone(out, "filer-side filing must be rejected")

    def test_accepts_schedule_label(self):
        # Regression: EDGAR labels 2024+ 13Gs as 'SCHEDULE 13G', not 'SC 13G'.
        recorded_filter = []
        def stub_list(cik, types=None, **kw):
            if types:
                recorded_filter.extend(types)
            return [{"accession": "0001-26-1", "date": "2026-04-28",
                     "type": "SCHEDULE 13G",
                     "primary_doc_url": "https://example/p.htm",
                     "exhibits_index_url": "https://example/",
                     "title": "SCHEDULE 13G", "items": [],
                     "primary_doc": "p.htm"}]
        stock._edgar_list_filings = stub_list
        # Stub returns AMPX-as-subject XML (matches the ticker entity 'NVIDIA CORP'
        # from setUp? No — setUp uses NVIDIA. Override entity for this test.)
        stock._edgar_cik = lambda t: ("0001899287", "AMPRIUS TECHNOLOGIES INC")
        stock._edgar_get_html = lambda url: (
            (b'<x><issuerName>AMPRIUS TECHNOLOGIES INC</issuerName>'
             b'<issuerCusipNumber>03214Q108</issuerCusipNumber></x>')
            if url.endswith(".xml")
            else b"<html>fallback</html>"
        )
        out = stock._cusip_from_13g("AMPX")
        self.assertEqual(out, "03214Q108")
        self.assertIn("SCHEDULE 13G", recorded_filter)
        self.assertIn("SCHEDULE 13G/A", recorded_filter)

    def test_html_fallback_when_xml_404(self):
        # Pre-2024 filings: primary_doc.xml 404s, fall through to HTML cover.
        # HTML cover must also pass subject validation (entity name appears
        # in first ~4KB of normalized text).
        import urllib.error
        def fake_html(url):
            if url.endswith("primary_doc.xml"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return b"<html><body>NVIDIA CORP filed Schedule 13G. CUSIP No. 67066G104 (Common Stock)</body></html>"
        stock._edgar_get_html = fake_html
        # _html_to_text must include entity name for subject substring check
        stock._html_to_text = lambda raw: "NVIDIA CORP filed Schedule 13G. CUSIP No. 67066G104 (Common Stock)"
        out = stock._cusip_from_13g("NVDA")
        self.assertEqual(out, "67066G104")

    def test_cusip_cached_on_hit(self):
        calls = []
        def fake_html(url):
            calls.append(url)
            if url.endswith("primary_doc.xml"):
                return b'<x><issuerCusipNumber>67066G104</issuerCusipNumber></x>'
            return b""
        stock._edgar_get_html = fake_html
        stock._cusip_from_13g("NVDA")
        n1 = len(calls)
        stock._cusip_from_13g("NVDA")  # should be cache hit
        self.assertEqual(len(calls), n1, "second call must hit cache, no HTTP")

    def test_cusip_cached_on_miss(self):
        # If nothing found, cache None to avoid retrying.
        import urllib.error
        calls = []
        def fake_html(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        stock._edgar_get_html = fake_html
        stock._html_to_text = lambda raw: ""
        out = stock._cusip_from_13g("NVDA")
        self.assertIsNone(out)
        n1 = len(calls)
        stock._cusip_from_13g("NVDA")  # second call should be cache hit (None cached)
        self.assertEqual(len(calls), n1)

    def test_miss_ttl_shorter_than_hit_ttl(self):
        # Locks in: a one-off EFTS/SEC hiccup that gets cached as None must
        # expire much sooner than a confirmed-real CUSIP. Otherwise a
        # transient blip pins a wrong 'no CUSIP' answer for ~30 days.
        import time as _time, json as _json
        import urllib.error

        def fake_hit(url):
            if url.endswith("primary_doc.xml"):
                return (b'<x><issuerName>NVIDIA CORP</issuerName>'
                        b'<issuerCusipNumber>67066G104</issuerCusipNumber></x>')
            return b""
        stock._edgar_get_html = fake_hit
        stock._cusip_from_13g("NVDA")
        with open(os.path.join(stock.CACHE_DIR, "cusip_NVDA.json")) as f:
            hit_entry = _json.load(f)
        hit_ttl_days = (hit_entry["expires_at"] - hit_entry["cached_at"]) / 86400

        def fake_miss(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        stock._edgar_get_html = fake_miss
        stock._html_to_text = lambda raw: ""
        stock._cusip_from_13g("FAKE")
        with open(os.path.join(stock.CACHE_DIR, "cusip_FAKE.json")) as f:
            miss_entry = _json.load(f)
        miss_ttl_days = (miss_entry["expires_at"] - miss_entry["cached_at"]) / 86400

        self.assertGreaterEqual(hit_ttl_days, 29, "positive cache should last ~30d")
        self.assertLessEqual(miss_ttl_days, 2, "miss cache should be ≤2d")


class TestNormalizeDelStripping(unittest.TestCase):
    """Locked in after BRK-B failure: 13F appends state-of-incorporation
    marker 'DEL' to Delaware-incorporated companies. Must be stripped so the
    normalized form matches OpenFIGI's shorter name."""

    def test_strips_trailing_del(self):
        self.assertEqual(stock._normalize_issuer("BERKSHIRE HATHAWAY INC DEL"),
                         stock._normalize_issuer("BERKSHIRE HATHAWAY INC"))

    def test_strips_trailing_delaware(self):
        self.assertEqual(stock._normalize_issuer("FOO INC DELAWARE"),
                         stock._normalize_issuer("FOO INC"))

    def test_strips_trailing_new(self):
        # 'NEW' suffix indicates a post-reorganization issuance in 13F.
        self.assertEqual(stock._normalize_issuer("CHARTER COMMUNICATIONS INC N"),
                         "CHARTER COMMUNICATIONS INC N")  # 'N' is single letter, stripped
        self.assertEqual(stock._normalize_issuer("FOO CORP NEW"),
                         stock._normalize_issuer("FOO CORP"))

    def test_does_not_strip_del_in_middle(self):
        # Defensive: 'DEL' as a middle word in a hypothetical name stays.
        self.assertIn("DEL", stock._normalize_issuer("DEL MONTE FOODS INC"))


class TestCusipLookupCommand(unittest.TestCase):
    """End-to-end stub of cusip_lookup — verifies it aggregates all sources."""

    def setUp(self):
        self._orig_cik = stock._edgar_cik
        self._orig_13g = stock._cusip_from_13g
        self._orig_figi = stock._openfigi_resolve_ticker
        stock._edgar_cik = lambda t: ("0001045810", "NVIDIA CORP")
        stock._cusip_from_13g = lambda t: "67066G104"
        stock._openfigi_resolve_ticker = lambda t: {
            "name": "NVIDIA CORP", "figi": "BBG000BBJQV0",
            "description": "NVDA"}

    def tearDown(self):
        stock._edgar_cik = self._orig_cik
        stock._cusip_from_13g = self._orig_13g
        stock._openfigi_resolve_ticker = self._orig_figi

    def test_aggregates_all_identifiers(self):
        out = stock.cusip_lookup("NVDA")
        self.assertEqual(out["ticker"], "NVDA")
        self.assertEqual(out["cik"], "0001045810")
        self.assertEqual(out["cusip"], "67066G104")
        self.assertEqual(out["figi"], "BBG000BBJQV0")
        self.assertEqual(out["openfigi_name"], "NVIDIA CORP")
        self.assertNotIn("error", out)

    def test_unknown_ticker_errors(self):
        stock._edgar_cik = lambda t: (None, None)
        stock._cusip_from_13g = lambda t: None
        stock._openfigi_resolve_ticker = lambda t: None
        # yfinance call inside cusip_lookup might still try — patch ISIN too
        orig_ticker = stock.yf.Ticker
        class FakeT:
            isin = "-"
        stock.yf.Ticker = lambda x: FakeT()
        try:
            out = stock.cusip_lookup("NOTAREALSTOCK")
            self.assertIn("error", out)
        finally:
            stock.yf.Ticker = orig_ticker


class TestFilingTextCaching(unittest.TestCase):
    """Lock in spec §4 requirement: document fetches cached 7 days."""

    def test_filing_text_uses_cache_for_document_fetch(self):
        """The same document URL should only be fetched once across calls."""
        # stock module is loaded at module level via importlib
        # Stub everything except _cached (which we want to actually exercise)
        orig_cik = stock._edgar_cik
        orig_get = stock._edgar_get
        orig_get_html = stock._edgar_get_html
        fetch_count = [0]
        fake_sub = {
            "filings": {"recent": {
                "form": ["10-K"],
                "filingDate": ["2026-01-01"],
                "accessionNumber": ["0001-26-99"],
                "primaryDocument": ["test.htm"],
                "primaryDocDescription": ["10-K"],
                "items": [""],
            }}
        }
        def fake_html(url):
            fetch_count[0] += 1
            return b"<html><body>Item 7. Management's Discussion and Analysis test test test. Item 7A. End.</body></html>"
        stock._edgar_cik = lambda t: ("0000000099", "TEST")
        stock._edgar_get = lambda url: fake_sub
        stock._edgar_get_html = fake_html
        # Clear cache to ensure clean state
        try:
            import shutil, os
            cache_dir = os.path.expanduser("~/.stock-prices/cache")
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
        except Exception:
            pass
        try:
            stock.filing_text("TEST", form_type="10-K", section="mda")
            first_count = fetch_count[0]
            stock.filing_text("TEST", form_type="10-K", section="mda")
            second_count = fetch_count[0]
            # Second call should NOT have fetched again
            self.assertEqual(second_count, first_count,
                             f"Expected cached fetch on 2nd call, but fetch_count went {first_count} → {second_count}")
        finally:
            stock._edgar_cik = orig_cik
            stock._edgar_get = orig_get
            stock._edgar_get_html = orig_get_html


if __name__ == "__main__":
    unittest.main(verbosity=2)
