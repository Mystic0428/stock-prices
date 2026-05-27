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

    def test_ttm_balance_sheet_rejected(self):
        # Balance sheets are point-in-time; TTM is meaningless. Returns before
        # any network call.
        out = stock.financials("AAPL", statement="balance", ttm=True)
        self.assertIn("error", out)
        self.assertIn("TTM balance", out["error"])


def _run_cli(*args):
    out = subprocess.run([PYTHON, SCRIPT, *args],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout


def _skip_if_unavailable(payload):
    """Skip live tests when Yahoo is rate-limiting or unreachable."""
    err = payload.get("error", "") if isinstance(payload, dict) else ""
    if "rate limited" in err or "Failed" in err or "connection" in err.lower():
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
