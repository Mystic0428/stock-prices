#!/usr/bin/env python3
"""Stock data CLI backed by yfinance. Outputs JSON for easy LLM consumption."""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

DATA_DIR = os.path.expanduser("~/.stock-prices")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
CACHE_TTL_SECONDS = 300       # 5 min — live quotes/info during market hours
CACHE_TTL_HISTORY = 900       # 15 min — OHLCV bars (intraday can move; keep modest)
CACHE_TTL_FUNDAMENTALS = 3600  # 1 hour — financials/returns change slowly


def _round(v, n=2):
    return round(float(v), n) if v is not None else None


def _format_error(e):
    """Translate yfinance exceptions into actionable messages for Claude."""
    from yfinance.exceptions import (
        YFRateLimitError, YFTickerMissingError, YFPricesMissingError,
    )
    if isinstance(e, YFRateLimitError):
        return "rate limited by Yahoo Finance — wait 1-2 minutes and retry"
    if isinstance(e, YFTickerMissingError):
        return f"ticker not found: {e}"
    if isinstance(e, YFPricesMissingError):
        return f"no price data available: {e}"
    msg = str(e)
    if "429" in msg or "Too Many Requests" in msg or "rate limit" in msg.lower():
        return f"rate limited — wait 1-2 minutes and retry. Detail: {msg}"
    return msg


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(filename, default):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(filename, data):
    _ensure_data_dir()
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def _cached(key, ttl, fn):
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                entry = json.load(f)
            if entry.get("expires_at", 0) > time.time():
                return entry["data"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    data = fn()
    if not (isinstance(data, dict) and "error" in data):
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump({"data": data,
                           "expires_at": time.time() + ttl,
                           "cached_at": time.time()}, f)
        except OSError:
            pass
    return data


def _download_closes(tickers, period):
    """Batch-fetch closing prices for several tickers in one HTTP round-trip.

    Returns a {TICKER: pandas.Series} dict (uppercased keys). Tickers with no
    data are omitted. One call instead of N reduces rate-limit pressure.
    """
    up = [t.upper() for t in tickers]
    data = yf.download(up, period=period, progress=False, auto_adjust=False)
    if data is None or data.empty:
        return {}
    closes = data["Close"]  # DataFrame, columns are uppercased tickers
    out = {}
    for t in up:
        if t in closes.columns:
            s = closes[t].dropna()
            if not s.empty:
                out[t] = s
    return out


def _quote_one(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", auto_adjust=False)

        if hist.empty:
            return {"ticker": ticker.upper(), "error": "no data (ticker may be invalid)"}

        current = hist.iloc[-1]
        prev_close = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else float(current["Close"])
        price = float(current["Close"])
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        market_cap = None
        currency = "USD"
        try:
            fi = t.fast_info
            market_cap = fi.get("marketCap")
            currency = fi.get("currency") or "USD"
        except Exception:
            pass

        result = {
            "ticker": ticker.upper(),
            "price": _round(price),
            "change": _round(change),
            "change_pct": _round(change_pct),
            "volume": int(current["Volume"]),
            "day_high": _round(current["High"]),
            "day_low": _round(current["Low"]),
            "prev_close": _round(prev_close),
            "currency": currency,
            "timestamp": current.name.isoformat(),
        }
        if market_cap:
            result["market_cap"] = int(market_cap)
        return result
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def quote(tickers, use_cache=True):
    if use_cache:
        results = [_cached(f"quote_{t.upper()}", CACHE_TTL_SECONDS,
                           lambda tt=t: _quote_one(tt)) for t in tickers]
    else:
        results = [_quote_one(t) for t in tickers]
    return results[0] if len(results) == 1 else results


def _info_one(ticker):
    try:
        t = yf.Ticker(ticker)
        i = t.info
        if not i or not i.get("symbol"):
            return {"ticker": ticker.upper(), "error": "no data (ticker may be invalid)"}

        return {
            "ticker": ticker.upper(),
            "name": i.get("longName") or i.get("shortName"),
            "sector": i.get("sector"),
            "industry": i.get("industry"),
            "country": i.get("country"),
            "website": i.get("website"),
            "exchange": i.get("exchange"),
            "currency": i.get("currency"),
            "market_cap": i.get("marketCap"),
            "enterprise_value": i.get("enterpriseValue"),
            "pe_ratio": i.get("trailingPE"),
            "forward_pe": i.get("forwardPE"),
            "peg_ratio": i.get("pegRatio"),
            "price_to_book": i.get("priceToBook"),
            "price_to_sales": i.get("priceToSalesTrailing12Months"),
            "ev_to_revenue": i.get("enterpriseToRevenue"),
            "ev_to_ebitda": i.get("enterpriseToEbitda"),
            "eps": i.get("trailingEps"),
            "forward_eps": i.get("forwardEps"),
            "book_value_per_share": i.get("bookValue"),
            "dividend_yield": i.get("dividendYield"),
            "dividend_rate": i.get("dividendRate"),
            "payout_ratio": i.get("payoutRatio"),
            "beta": i.get("beta"),
            "52_week_high": i.get("fiftyTwoWeekHigh"),
            "52_week_low": i.get("fiftyTwoWeekLow"),
            "50_day_avg": i.get("fiftyDayAverage"),
            "200_day_avg": i.get("twoHundredDayAverage"),
            "avg_volume": i.get("averageVolume"),
            "shares_outstanding": i.get("sharesOutstanding"),
            "float_shares": i.get("floatShares"),
            "revenue": i.get("totalRevenue"),
            "revenue_growth": i.get("revenueGrowth"),
            "earnings_growth": i.get("earningsGrowth"),
            "earnings_quarterly_growth": i.get("earningsQuarterlyGrowth"),
            "ebitda": i.get("ebitda"),
            "ebitda_margin": i.get("ebitdaMargins"),
            "gross_profits": i.get("grossProfits"),
            "gross_margin": i.get("grossMargins"),
            "profit_margin": i.get("profitMargins"),
            "operating_margin": i.get("operatingMargins"),
            "return_on_equity": i.get("returnOnEquity"),
            "return_on_assets": i.get("returnOnAssets"),
            "free_cashflow": i.get("freeCashflow"),
            "operating_cashflow": i.get("operatingCashflow"),
            "total_cash": i.get("totalCash"),
            "total_cash_per_share": i.get("totalCashPerShare"),
            "total_debt": i.get("totalDebt"),
            "debt_to_equity": i.get("debtToEquity"),
            "quick_ratio": i.get("quickRatio"),
            "current_ratio": i.get("currentRatio"),
            "analyst_target_mean": i.get("targetMeanPrice"),
            "analyst_target_high": i.get("targetHighPrice"),
            "analyst_target_low": i.get("targetLowPrice"),
            "analyst_count": i.get("numberOfAnalystOpinions"),
            "analyst_recommendation": i.get("recommendationKey"),
            "analyst_recommendation_mean": i.get("recommendationMean"),
            "held_pct_insiders": i.get("heldPercentInsiders"),
            "held_pct_institutions": i.get("heldPercentInstitutions"),
            "short_ratio": i.get("shortRatio"),
            "short_pct_of_float": i.get("shortPercentOfFloat"),
            "description": i.get("longBusinessSummary"),
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def info(ticker, use_cache=True):
    if use_cache:
        return _cached(f"info_{ticker.upper()}", CACHE_TTL_SECONDS, lambda: _info_one(ticker))
    return _info_one(ticker)


def history(ticker, period=None, start=None, end=None, interval="1d"):
    try:
        t = yf.Ticker(ticker)
        if start or end:
            hist = t.history(start=start, end=end, interval=interval, auto_adjust=False)
        else:
            hist = t.history(period=period or "1mo", interval=interval, auto_adjust=False)

        if hist.empty:
            return {"ticker": ticker.upper(), "error": "no history data (check ticker/date range)"}

        bars = [
            {
                "date": idx.isoformat(),
                "open": _round(row["Open"]),
                "high": _round(row["High"]),
                "low": _round(row["Low"]),
                "close": _round(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for idx, row in hist.iterrows()
        ]

        return {
            "ticker": ticker.upper(),
            "interval": interval,
            "count": len(bars),
            "bars": bars,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def dividends(ticker):
    try:
        s = yf.Ticker(ticker).dividends
        if s is None or len(s) == 0:
            return {"ticker": ticker.upper(), "dividends": [], "note": "no dividend history"}
        return {
            "ticker": ticker.upper(),
            "count": len(s),
            "dividends": [
                {"date": idx.isoformat(), "amount": _round(amt, 4)}
                for idx, amt in s.items()
            ],
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def splits(ticker):
    try:
        s = yf.Ticker(ticker).splits
        if s is None or len(s) == 0:
            return {"ticker": ticker.upper(), "splits": [], "note": "no split history"}
        return {
            "ticker": ticker.upper(),
            "count": len(s),
            "splits": [
                {"date": idx.isoformat(), "ratio": _round(ratio, 4)}
                for idx, ratio in s.items()
            ],
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def earnings(ticker, limit=12):
    try:
        df = yf.Ticker(ticker).earnings_dates
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "earnings": [], "note": "no earnings data"}

        df = df.head(limit)
        items = []
        for idx, row in df.iterrows():
            est = row.get("EPS Estimate")
            rep = row.get("Reported EPS")
            surp = row.get("Surprise(%)")
            items.append({
                "date": idx.isoformat(),
                "eps_estimate": _round(est, 3) if est == est else None,  # NaN check
                "reported_eps": _round(rep, 3) if rep == rep else None,
                "surprise_pct": _round(surp, 2) if surp == surp else None,
            })

        return {
            "ticker": ticker.upper(),
            "count": len(items),
            "earnings": items,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def financials(ticker, statement="income", quarterly=False, ttm=False):
    try:
        t = yf.Ticker(ticker)
        period_type = "ttm" if ttm else ("quarterly" if quarterly else "annual")

        if ttm and statement == "balance":
            return {"ticker": ticker.upper(),
                    "error": "no TTM balance sheet — balance sheets are point-in-time "
                             "(use annual or --quarterly instead)"}

        attr = {
            ("income", "annual"): "income_stmt",
            ("income", "quarterly"): "quarterly_income_stmt",
            ("income", "ttm"): "ttm_income_stmt",
            ("balance", "annual"): "balance_sheet",
            ("balance", "quarterly"): "quarterly_balance_sheet",
            ("cashflow", "annual"): "cashflow",
            ("cashflow", "quarterly"): "quarterly_cashflow",
            ("cashflow", "ttm"): "ttm_cashflow",
        }.get((statement, period_type))

        if attr is None:
            return {"ticker": ticker.upper(),
                    "error": f"invalid statement '{statement}' (use income/balance/cashflow)"}

        df = getattr(t, attr)
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "statement": statement,
                    "period_type": period_type,
                    "data": {}, "note": "no data"}

        periods = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df.columns]
        data = {}
        for row_name, row in df.iterrows():
            values = []
            for v in row.values:
                if v != v or v is None:  # NaN
                    values.append(None)
                else:
                    values.append(float(v))
            data[str(row_name)] = values

        return {
            "ticker": ticker.upper(),
            "statement": statement,
            "period_type": period_type,
            "periods": periods,
            "data": data,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def recommendations(ticker):
    try:
        df = yf.Ticker(ticker).recommendations
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "recommendations": [], "note": "no recommendations data"}

        items = df.to_dict(orient="records")
        return {
            "ticker": ticker.upper(),
            "note": "period 0m=current month, -1m=last month, etc. Counts are number of analysts.",
            "recommendations": items,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def _period_return(close, days):
    if len(close) < 2:
        return None
    target_date = close.index[-1] - pd.Timedelta(days=days)
    past = close[close.index <= target_date]
    if past.empty:
        return None
    start = float(past.iloc[-1])
    end = float(close.iloc[-1])
    return round((end / start - 1) * 100, 2) if start else None


def _ytd_return(close):
    if len(close) < 2:
        return None
    year_start = pd.Timestamp(year=close.index[-1].year, month=1, day=1, tz=close.index.tz)
    in_year = close[close.index >= year_start]
    if len(in_year) < 2:
        return None
    return round((float(in_year.iloc[-1]) / float(in_year.iloc[0]) - 1) * 100, 2)


def compare(tickers):
    try:
        # One batched call for YTD history; per-ticker .info can't be batched.
        try:
            ytd_closes = _download_closes(tickers, "ytd")
        except Exception:
            ytd_closes = {}

        rows = []
        for ticker in tickers:
            t = yf.Ticker(ticker)
            try:
                i = t.info or {}
                close = ytd_closes.get(ticker.upper())
                ytd = _ytd_return(close) if close is not None else None
                rows.append({
                    "ticker": ticker.upper(),
                    "name": i.get("shortName") or i.get("longName"),
                    "price": _round(i.get("currentPrice") or i.get("regularMarketPrice")),
                    "market_cap": i.get("marketCap"),
                    "pe_ratio": _round(i.get("trailingPE"), 2),
                    "forward_pe": _round(i.get("forwardPE"), 2),
                    "eps": _round(i.get("trailingEps"), 2),
                    "dividend_yield_pct": _round(i.get("dividendYield"), 2),
                    "beta": _round(i.get("beta"), 2),
                    "52_week_high": _round(i.get("fiftyTwoWeekHigh"), 2),
                    "52_week_low": _round(i.get("fiftyTwoWeekLow"), 2),
                    "ytd_return_pct": ytd,
                    "profit_margin_pct": _round((i.get("profitMargins") or 0) * 100, 2) if i.get("profitMargins") else None,
                    "return_on_equity_pct": _round((i.get("returnOnEquity") or 0) * 100, 2) if i.get("returnOnEquity") else None,
                })
            except Exception as e:
                rows.append({"ticker": ticker.upper(), "error": _format_error(e)})
        return {"tickers": [t.upper() for t in tickers], "count": len(rows), "comparison": rows}
    except Exception as e:
        return {"error": _format_error(e)}


def _compute_returns(close):
    periods = {
        "1d": 1, "1w": 7, "1mo": 30, "3mo": 91, "6mo": 182,
        "1y": 365, "3y": 365 * 3, "5y": 365 * 5, "10y": 365 * 10,
    }
    result = {p: _period_return(close, d) for p, d in periods.items()}
    result["ytd"] = _ytd_return(close)
    return result


def returns(ticker, vs=None):
    try:
        hist = yf.Ticker(ticker).history(period="max", auto_adjust=False)
        if hist.empty:
            return {"ticker": ticker.upper(), "error": "no price data"}

        close = hist["Close"]
        ticker_returns = _compute_returns(close)

        out = {
            "ticker": ticker.upper(),
            "as_of": close.index[-1].isoformat(),
            "current_price": _round(float(close.iloc[-1])),
            "returns_pct": ticker_returns,
        }

        if vs:
            bench_hist = yf.Ticker(vs).history(period="max", auto_adjust=False)
            if bench_hist.empty:
                out["benchmark_error"] = f"no data for benchmark {vs.upper()}"
            else:
                bench_returns = _compute_returns(bench_hist["Close"])
                excess = {}
                for k, v in ticker_returns.items():
                    b = bench_returns.get(k)
                    excess[k] = _round(v - b) if (v is not None and b is not None) else None
                out["benchmark"] = vs.upper()
                out["benchmark_returns_pct"] = bench_returns
                out["excess_returns_pct"] = excess
                out["note"] = f"excess_returns_pct = {ticker.upper()} return minus {vs.upper()} return for each horizon"

        return out
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def indicators(ticker, period="1y"):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=False)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker.upper(), "error": f"insufficient data for period {period}"}

        close = hist["Close"]

        sma_20 = close.rolling(20).mean().iloc[-1]
        sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
        sma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
        ema_12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
        ema_26 = close.ewm(span=26, adjust=False).mean().iloc[-1]

        macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line

        bb_period = 20
        bb_mid = close.rolling(bb_period).mean()
        bb_std = close.rolling(bb_period).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        rsi_val = _rsi(close, 14).iloc[-1]

        price = float(close.iloc[-1])
        return {
            "ticker": ticker.upper(),
            "as_of": close.index[-1].isoformat(),
            "period": period,
            "price": _round(price),
            "sma": {
                "20": _round(sma_20),
                "50": _round(sma_50) if sma_50 is not None else None,
                "200": _round(sma_200) if sma_200 is not None else None,
            },
            "ema": {
                "12": _round(ema_12),
                "26": _round(ema_26),
            },
            "rsi_14": _round(rsi_val) if rsi_val == rsi_val else None,
            "macd": {
                "macd": _round(macd_line.iloc[-1], 4),
                "signal": _round(signal_line.iloc[-1], 4),
                "histogram": _round(macd_hist.iloc[-1], 4),
            },
            "bollinger_20_2": {
                "upper": _round(bb_upper.iloc[-1]),
                "middle": _round(bb_mid.iloc[-1]),
                "lower": _round(bb_lower.iloc[-1]),
                "pct_b": _round((price - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) * 100, 2)
                         if bb_upper.iloc[-1] != bb_lower.iloc[-1] else None,
            },
            "interpretation_hints": {
                "rsi": "RSI > 70 overbought, < 30 oversold",
                "macd": "MACD above signal = bullish momentum; histogram sign change = potential crossover",
                "bollinger_pct_b": "0 = at lower band, 50 = at middle, 100 = at upper band",
            },
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def volatility(ticker, period="1y", risk_free_rate=0.04):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=False)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker.upper(), "error": f"insufficient data for period {period}"}

        close = hist["Close"]
        ret = close.pct_change().dropna()

        annual_vol = float(ret.std() * np.sqrt(252) * 100)
        mean_daily = float(ret.mean() * 100)
        annualized_return = float(((1 + ret.mean()) ** 252 - 1) * 100)

        cum = (1 + ret).cumprod()
        running_max = cum.cummax()
        drawdown = (cum - running_max) / running_max
        max_dd = float(drawdown.min() * 100)

        excess_daily = ret - risk_free_rate / 252
        sharpe = float(excess_daily.mean() / ret.std() * np.sqrt(252)) if ret.std() else None

        return {
            "ticker": ticker.upper(),
            "period": period,
            "trading_days": len(ret),
            "annualized_volatility_pct": _round(annual_vol),
            "annualized_return_pct": _round(annualized_return),
            "mean_daily_return_pct": _round(mean_daily, 4),
            "max_drawdown_pct": _round(max_dd),
            "sharpe_ratio": _round(sharpe, 3) if sharpe is not None else None,
            "risk_free_rate_assumed": risk_free_rate,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def news(ticker, limit=10):
    try:
        items = yf.Ticker(ticker).news or []
        out = []
        for n in items[:limit]:
            c = n.get("content") or n
            cu = c.get("canonicalUrl") or c.get("clickThroughUrl") or {}
            prov = c.get("provider") or {}
            out.append({
                "title": c.get("title"),
                "summary": c.get("summary") or c.get("description"),
                "published": c.get("pubDate") or c.get("displayTime"),
                "provider": prov.get("displayName") if isinstance(prov, dict) else None,
                "url": cu.get("url") if isinstance(cu, dict) else None,
            })
        return {"ticker": ticker.upper(), "count": len(out), "news": out}
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def watchlist(action="show", tickers=None):
    data = _load_json("watchlist.json", {"tickers": []})
    current = data.get("tickers", [])
    tickers = [t.upper() for t in (tickers or [])]

    if action == "show":
        if not current:
            return {"watchlist": [], "note": "watchlist is empty. Add tickers with `watchlist add AAPL MSFT ...`"}
        quotes = quote(current)
        if not isinstance(quotes, list):
            quotes = [quotes]
        return {"count": len(current), "tickers": current, "quotes": quotes}

    if action == "add":
        if not tickers:
            return {"error": "specify at least one ticker to add"}
        added = [t for t in tickers if t not in current]
        new_list = current + added
        _save_json("watchlist.json", {"tickers": new_list})
        return {"action": "add", "added": added,
                "already_in_watchlist": [t for t in tickers if t in current],
                "watchlist": new_list}

    if action == "remove":
        if not tickers:
            return {"error": "specify at least one ticker to remove"}
        removed = [t for t in tickers if t in current]
        new_list = [t for t in current if t not in tickers]
        _save_json("watchlist.json", {"tickers": new_list})
        return {"action": "remove", "removed": removed,
                "not_in_watchlist": [t for t in tickers if t not in current],
                "watchlist": new_list}

    if action == "clear":
        _save_json("watchlist.json", {"tickers": []})
        return {"action": "clear", "previous_count": len(current), "watchlist": []}

    return {"error": f"unknown action '{action}'"}


def etf(ticker):
    try:
        t = yf.Ticker(ticker)
        try:
            fd = t.funds_data
        except Exception as e:
            return {"ticker": ticker.upper(),
                    "error": f"not a fund/ETF, or no fund data available: {_format_error(e)}"}

        result = {"ticker": ticker.upper()}

        try:
            result["overview"] = fd.fund_overview
        except Exception:
            pass
        try:
            result["asset_classes"] = fd.asset_classes
        except Exception:
            pass
        try:
            result["sector_weightings"] = fd.sector_weightings
        except Exception:
            pass

        try:
            holdings = fd.top_holdings
            if holdings is not None and not holdings.empty:
                result["top_holdings"] = [
                    {"symbol": idx, "name": row["Name"], "weight_pct": _round(row["Holding Percent"] * 100, 3)}
                    for idx, row in holdings.iterrows()
                ]
        except Exception:
            pass

        fund_keys = ("overview", "asset_classes", "sector_weightings", "top_holdings")
        if not any(k in result for k in fund_keys):
            return {"ticker": ticker.upper(),
                    "error": f"no ETF/fund data — {ticker.upper()} is likely a regular stock (use `info` instead)"}

        try:
            i = t.info or {}
            result["expense_ratio"] = i.get("netExpenseRatio") or i.get("annualReportExpenseRatio")
            result["total_assets"] = i.get("totalAssets")
            result["yield"] = i.get("yield")
            result["nav_price"] = i.get("navPrice")
            result["category"] = i.get("category")
            result["fund_family"] = i.get("fundFamily")
        except Exception:
            pass

        try:
            result["description"] = fd.description
        except Exception:
            pass

        try:
            cg = t.capital_gains  # capital-gains distributions (funds only; often empty)
            if cg is not None and len(cg):
                result["capital_gains"] = [
                    {"date": idx.isoformat(), "amount": _round(amt, 4)}
                    for idx, amt in cg.sort_index().items()
                ]
        except Exception:
            pass

        return result
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def insiders(ticker, limit=20):
    try:
        t = yf.Ticker(ticker)
        out = {"ticker": ticker.upper()}

        try:
            summary = t.insider_purchases
            if summary is not None and not summary.empty:
                rows = {}
                for _, row in summary.iterrows():
                    label = row.iloc[0]
                    shares = row.iloc[1] if pd.notna(row.iloc[1]) else None
                    trans = row.iloc[2] if pd.notna(row.iloc[2]) else None
                    rows[str(label)] = {"shares": float(shares) if shares is not None else None,
                                        "transactions": int(trans) if trans is not None else None}
                out["summary_6mo"] = rows
        except Exception:
            pass

        try:
            it = t.insider_transactions
            if it is not None and not it.empty:
                trans = []
                for _, row in it.head(limit).iterrows():
                    trans.append({
                        "date": str(row["Start Date"])[:10],
                        "insider": row.get("Insider"),
                        "position": row.get("Position"),
                        "ownership": row.get("Ownership"),
                        "shares": int(row["Shares"]) if pd.notna(row.get("Shares")) else None,
                        "value_usd": float(row["Value"]) if pd.notna(row.get("Value")) else None,
                        "description": row.get("Text"),
                    })
                out["transactions"] = trans
                out["transactions_count"] = len(trans)
        except Exception:
            pass

        if "summary_6mo" not in out and "transactions" not in out:
            return {"ticker": ticker.upper(), "error": "no insider data available"}

        return out
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def cache_cmd(action="show"):
    if not os.path.exists(CACHE_DIR):
        return {"cache_dir": CACHE_DIR, "entries": 0,
                "note": "cache directory does not exist yet"}

    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]

    if action == "clear":
        removed = 0
        for f in files:
            try:
                os.unlink(os.path.join(CACHE_DIR, f))
                removed += 1
            except OSError:
                pass
        return {"action": "clear", "removed": removed}

    now = time.time()
    valid = 0
    expired = 0
    total_size = 0
    oldest = None
    entries = []
    for f in files:
        path = os.path.join(CACHE_DIR, f)
        try:
            size = os.path.getsize(path)
            total_size += size
            with open(path) as fh:
                entry = json.load(fh)
            exp = entry.get("expires_at", 0)
            cached_at = entry.get("cached_at", 0)
            if cached_at and (oldest is None or cached_at < oldest):
                oldest = cached_at
            ttl_left = exp - now
            entries.append({
                "key": f[:-5],  # strip .json
                "size_bytes": size,
                "ttl_left_seconds": round(ttl_left),
                "expired": ttl_left <= 0,
            })
            if ttl_left > 0:
                valid += 1
            else:
                expired += 1
        except Exception:
            pass

    return {
        "cache_dir": CACHE_DIR,
        "ttl_seconds": CACHE_TTL_SECONDS,
        "entries_count": len(files),
        "valid": valid,
        "expired": expired,
        "total_size_bytes": total_size,
        "oldest_entry_age_seconds": round(now - oldest) if oldest else None,
        "entries": entries,
    }


def chart(tickers, period="1y", ma=None, out=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return {"error": "matplotlib not installed. Run: pip install matplotlib"}

    try:
        tickers = [t.upper() for t in tickers]

        if out is None:
            ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
            tag = "_".join(tickers) if len(tickers) <= 3 else f"{len(tickers)}tickers"
            out = os.path.join(DATA_DIR, "charts", f"{tag}_{period}_{ts}.png")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

        fig, ax = plt.subplots(figsize=(12, 6))

        if len(tickers) == 1:
            ticker = tickers[0]
            hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if hist.empty:
                plt.close(fig)
                return {"error": f"no data for {ticker}"}

            close = hist["Close"]
            ax.plot(close.index, close, label=f"{ticker}", linewidth=2, color="#1f77b4")

            if ma:
                colors = ["#ff7f0e", "#2ca02c", "#d62728"]
                for i, window in enumerate(ma):
                    if len(close) >= window:
                        ma_line = close.rolling(window).mean()
                        ax.plot(ma_line.index, ma_line,
                                label=f"SMA-{window}",
                                alpha=0.75,
                                linewidth=1.5,
                                color=colors[i % len(colors)])

            ax.set_title(f"{ticker} — last {period}", fontsize=14, fontweight="bold")
            ax.set_ylabel("Price (USD)")

            current = float(close.iloc[-1])
            start = float(close.iloc[0])
            ret = (current / start - 1) * 100
            subtitle = f"Last: ${current:.2f}  |  Period return: {ret:+.2f}%"
            ax.text(0.99, 0.02, subtitle, transform=ax.transAxes,
                    ha="right", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.8))
        else:
            for ticker in tickers:
                hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
                if hist.empty:
                    continue
                close = hist["Close"]
                normalized = close / close.iloc[0] * 100
                ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
                ax.plot(normalized.index, normalized,
                        label=f"{ticker} ({ret:+.1f}%)", linewidth=2)
            ax.set_title(f"Normalized comparison — last {period} (base = 100)",
                         fontsize=14, fontweight="bold")
            ax.set_ylabel("Normalized return (start = 100)")
            ax.axhline(100, color="gray", linestyle="--", alpha=0.5, linewidth=1)

        ax.legend(loc="best", framealpha=0.9)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.autofmt_xdate()
        plt.tight_layout()
        plt.savefig(out, dpi=110)
        plt.close(fig)

        return {
            "tickers": tickers,
            "period": period,
            "moving_averages": ma,
            "chart_path": out,
            "note": "PNG saved. In Claude Code, show this path to display the chart inline.",
        }
    except Exception as e:
        return {"error": _format_error(e), "tickers": tickers}


def portfolio(action="show", ticker=None, shares=None, cost=None):
    data = _load_json("portfolio.json", {"positions": []})
    positions = data.get("positions", [])

    if action == "show":
        if not positions:
            return {"positions": [], "note": "portfolio is empty. Add positions with `portfolio add <ticker> <shares> [--cost <price>]`"}

        tickers = [p["ticker"] for p in positions]
        quotes = quote(tickers)
        if not isinstance(quotes, list):
            quotes = [quotes]
        by_ticker = {q.get("ticker"): q for q in quotes}

        rows = []
        total_value = 0.0
        total_cost = 0.0
        total_day_change = 0.0
        for p in positions:
            q = by_ticker.get(p["ticker"], {})
            price = q.get("price")
            change = q.get("change")
            row = {
                "ticker": p["ticker"],
                "shares": p["shares"],
                "cost_basis": p.get("cost_basis"),
                "current_price": price,
                "added": p.get("added"),
            }
            if price is not None:
                value = p["shares"] * price
                row["current_value"] = _round(value)
                total_value += value
                if change is not None:
                    day_chg = p["shares"] * change
                    row["day_change"] = _round(day_chg)
                    row["day_change_pct"] = q.get("change_pct")
                    total_day_change += day_chg
                if p.get("cost_basis") is not None:
                    cost_total = p["shares"] * p["cost_basis"]
                    pl = value - cost_total
                    row["cost"] = _round(cost_total)
                    row["unrealized_pl"] = _round(pl)
                    row["unrealized_pl_pct"] = _round(pl / cost_total * 100) if cost_total else None
                    total_cost += cost_total
            else:
                row["error"] = q.get("error", "no quote")
            rows.append(row)

        for row in rows:
            if row.get("current_value") and total_value:
                row["weight_pct"] = _round(row["current_value"] / total_value * 100)

        total_pl = total_value - total_cost if total_cost else None
        prev_total = total_value - total_day_change if total_day_change else total_value

        summary = {
            "total_value": _round(total_value),
            "day_change": _round(total_day_change),
            "day_change_pct": _round(total_day_change / prev_total * 100) if prev_total else None,
        }
        if total_cost:
            summary["total_cost"] = _round(total_cost)
            summary["total_unrealized_pl"] = _round(total_pl)
            summary["total_unrealized_pl_pct"] = _round(total_pl / total_cost * 100) if total_cost else None

        return {"summary": summary, "positions": rows}

    if action == "add":
        if not ticker or shares is None:
            return {"error": "usage: portfolio add <ticker> <shares> [--cost <basis>]"}
        ticker = ticker.upper()
        existing = next((p for p in positions if p["ticker"] == ticker), None)
        if existing:
            existing["shares"] = shares
            if cost is not None:
                existing["cost_basis"] = cost
            note = "updated existing position"
        else:
            positions.append({
                "ticker": ticker,
                "shares": shares,
                "cost_basis": cost,
                "added": pd.Timestamp.now().strftime("%Y-%m-%d"),
            })
            note = "added new position"
        _save_json("portfolio.json", {"positions": positions})
        return {"action": "add", "note": note, "ticker": ticker, "shares": shares, "cost_basis": cost,
                "positions_count": len(positions)}

    if action == "remove":
        if not ticker:
            return {"error": "usage: portfolio remove <ticker>"}
        ticker = ticker.upper()
        new_positions = [p for p in positions if p["ticker"] != ticker]
        if len(new_positions) == len(positions):
            return {"action": "remove", "note": f"{ticker} not in portfolio"}
        _save_json("portfolio.json", {"positions": new_positions})
        return {"action": "remove", "removed": ticker, "positions_count": len(new_positions)}

    if action == "clear":
        _save_json("portfolio.json", {"positions": []})
        return {"action": "clear", "previous_count": len(positions)}

    return {"error": f"unknown action '{action}'"}


def correlation(tickers, period="1y"):
    try:
        if len(tickers) < 2:
            return {"error": "need at least 2 tickers for correlation"}

        closes = _download_closes(tickers, period)
        missing = [t.upper() for t in tickers if t.upper() not in closes]
        if missing:
            return {"error": f"no data for {', '.join(missing)}"}

        df = pd.DataFrame(closes).dropna()
        if len(df) < 30:
            return {"error": f"insufficient overlapping data ({len(df)} days)"}

        ret = df.pct_change().dropna()
        corr = ret.corr().round(3)

        matrix = {row: {col: float(corr.loc[row, col]) for col in corr.columns}
                  for row in corr.index}

        return {
            "tickers": [t.upper() for t in tickers],
            "period": period,
            "trading_days": len(ret),
            "correlation_matrix": matrix,
            "note": "Pearson correlation of daily returns. 1.0 = perfectly correlated, 0 = uncorrelated, -1 = inversely correlated.",
        }
    except Exception as e:
        return {"error": _format_error(e)}


def _clean(v):
    """Coerce a pandas/numpy scalar into a JSON-safe native value (NaN -> None)."""
    if v is None:
        return None
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    if isinstance(v, float) and v != v:  # plain NaN
        return None
    return v


def _df_records(df, index_as=None, limit=None):
    """DataFrame -> list of JSON-safe dicts. index_as adds the row index under that key."""
    if df is None or getattr(df, "empty", True):
        return []
    if limit:
        df = df.head(limit)
    out = []
    for idx, row in df.iterrows():
        rec = {}
        if index_as:
            rec[index_as] = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        for col in df.columns:
            rec[str(col)] = _clean(row[col])
        out.append(rec)
    return out


def options(ticker, expiry=None, limit=40):
    try:
        t = yf.Ticker(ticker)
        expiries = list(t.options or [])
        if not expiries:
            return {"ticker": ticker.upper(), "error": "no options available for this ticker"}

        if expiry is None:
            expiry = expiries[0]  # nearest expiry
        elif expiry not in expiries:
            return {"ticker": ticker.upper(),
                    "error": f"expiry {expiry} not available",
                    "available_expiries": expiries}

        chain = t.option_chain(expiry)
        return {
            "ticker": ticker.upper(),
            "expiry": expiry,
            "available_expiries": expiries,
            "calls": _df_records(chain.calls, limit=limit),
            "puts": _df_records(chain.puts, limit=limit),
            "note": "impliedVolatility is a fraction (0.25 = 25%). inTheMoney is bool. limit applies per side.",
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def estimates(ticker):
    try:
        t = yf.Ticker(ticker)
        out = {"ticker": ticker.upper(),
               "note": "periods: 0q=current quarter, +1q=next quarter, 0y=current year, +1y=next year."}
        for key, attr in (("earnings_estimate", "earnings_estimate"),
                          ("revenue_estimate", "revenue_estimate"),
                          ("eps_trend", "eps_trend"),
                          ("eps_revisions", "eps_revisions"),
                          ("growth_estimates", "growth_estimates")):
            try:
                df = getattr(t, attr)
                if df is not None and not df.empty:
                    out[key] = _df_records(df, index_as="period")
            except Exception:
                pass
        if not any(k in out for k in ("earnings_estimate", "eps_trend")):
            return {"ticker": ticker.upper(), "error": "no analyst estimate data available"}
        return out
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def calendar_events(ticker):
    try:
        cal = yf.Ticker(ticker).calendar
        if not cal:
            return {"ticker": ticker.upper(), "error": "no calendar data available"}
        out = {"ticker": ticker.upper()}
        for k, v in cal.items():
            if isinstance(v, list):
                out[k] = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in v]
            elif hasattr(v, "isoformat"):
                out[k] = v.isoformat()
            else:
                out[k] = _clean(v)
        return out
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def ratings(ticker, limit=25):
    try:
        df = yf.Ticker(ticker).upgrades_downgrades
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "ratings": [], "note": "no upgrade/downgrade history"}
        df = df.sort_index(ascending=False)  # newest first
        return {
            "ticker": ticker.upper(),
            "count": min(len(df), limit),
            "total_available": len(df),
            "ratings": _df_records(df, index_as="date", limit=limit),
            "note": "Action: up/down/init/main/reit. currentPriceTarget is the firm's target.",
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def holders(ticker, limit=10):
    try:
        t = yf.Ticker(ticker)
        out = {"ticker": ticker.upper()}

        try:
            mh = t.major_holders
            if mh is not None and not mh.empty:
                out["major_holders"] = {str(idx): _clean(row.iloc[0]) for idx, row in mh.iterrows()}
        except Exception:
            pass
        for key, attr in (("institutional_holders", "institutional_holders"),
                          ("mutualfund_holders", "mutualfund_holders"),
                          ("insider_roster", "insider_roster_holders")):
            try:
                df = getattr(t, attr)
                if df is not None and not df.empty:
                    out[key] = _df_records(df, limit=limit)
            except Exception:
                pass

        if len(out) == 1:
            return {"ticker": ticker.upper(), "error": "no holder data available"}
        out["note"] = "pctHeld/percentHeld are fractions (0.65 = 65%)."
        return out
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


# SEC requires a User-Agent identifying the caller (with contact). Override
# with EDGAR_USER_AGENT to use your own email per SEC's fair-access policy.
EDGAR_UA = os.environ.get("EDGAR_USER_AGENT", "stock-prices-skill admin@example.com")

# Curated headline facts: (output key, [concept name fallbacks]). Concept
# naming varies by filer, so we try each until one is present.
EDGAR_FACTS = [
    ("revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]),
    ("gross_profit", ["GrossProfit"]),
    ("operating_income", ["OperatingIncomeLoss"]),
    ("net_income", ["NetIncomeLoss"]),
    ("eps_basic", ["EarningsPerShareBasic"]),
    ("eps_diluted", ["EarningsPerShareDiluted"]),
    ("total_assets", ["Assets"]),
    ("total_liabilities", ["Liabilities"]),
    ("stockholders_equity", ["StockholdersEquity"]),
    ("cash", ["CashAndCashEquivalentsAtCarryingValue"]),
]


def _edgar_get(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": EDGAR_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _edgar_cik(ticker):
    """Resolve a ticker to a zero-padded 10-digit CIK and company title."""
    mapping = _cached("edgar_ticker_map", 7 * 86400,
                      lambda: _edgar_get("https://www.sec.gov/files/company_tickers.json"))
    up = ticker.upper()
    for v in mapping.values():
        if str(v.get("ticker", "")).upper() == up:
            return str(v["cik_str"]).zfill(10), v.get("title")
    return None, None


def _edgar_annual(concept_data, n=5):
    """Latest n annual (10-K) values for a concept, deduped by fiscal-period end."""
    units = concept_data.get("units", {})
    unit_key = next(iter(units), None)
    if not unit_key:
        return [], None
    by_end = {}
    for x in units[unit_key]:
        if x.get("form") != "10-K":
            continue
        end = x.get("end")
        # keep the most recently filed value for each period end (handles restatements)
        if end not in by_end or x.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = x
    series = sorted(by_end.values(), key=lambda x: x.get("end", ""))[-n:]
    # Derive fiscal year from the period-end date — the filing's `fy` field is the
    # year the 10-K was filed, which mislabels prior-year comparatives.
    return [{"fiscal_year": int(x["end"][:4]) if x.get("end") else x.get("fy"),
             "end": x.get("end"), "value": x.get("val")} for x in series], unit_key


def edgar(ticker, concept=None, list_concepts=False):
    try:
        cik, title = _edgar_cik(ticker)
        if not cik:
            return {"ticker": ticker.upper(),
                    "error": f"ticker {ticker.upper()} not found in SEC EDGAR (US filers only)"}

        # Single-concept time series (annual + quarterly) via the concept endpoint.
        if concept:
            url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
            try:
                cc = _edgar_get(url)
            except Exception:
                return {"ticker": ticker.upper(), "cik": cik, "concept": concept,
                        "error": f"concept '{concept}' not found for this filer "
                                 f"(use `edgar {ticker.upper()} --list` to see available concepts)"}
            units = cc.get("units", {})
            unit_key = next(iter(units), None)
            rows = units.get(unit_key, []) if unit_key else []
            recent = [{"fp": x.get("fp"), "start": x.get("start"), "end": x.get("end"),
                       "value": x.get("val"), "form": x.get("form")}
                      for x in rows[-12:]]
            return {"ticker": ticker.upper(), "cik": cik, "entity": cc.get("entityName"),
                    "concept": concept, "label": cc.get("label"), "unit": unit_key,
                    "source": "SEC EDGAR XBRL (official)", "recent": recent}

        facts = _cached(f"edgar_facts_{cik}", 86400,
                        lambda: _edgar_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"))
        gaap = facts.get("facts", {}).get("us-gaap", {})

        # List available concept names so the caller knows what --concept accepts.
        if list_concepts:
            return {"ticker": ticker.upper(), "cik": cik, "entity": facts.get("entityName"),
                    "concept_count": len(gaap), "concepts": sorted(gaap.keys())}

        # Default: curated headline financials, latest annual values.
        out = {}
        for key, candidates in EDGAR_FACTS:
            for name in candidates:
                if name in gaap:
                    series, unit = _edgar_annual(gaap[name])
                    if series:
                        out[key] = {"concept": name, "unit": unit, "annual": series}
                    break
        if not out:
            return {"ticker": ticker.upper(), "cik": cik,
                    "error": "no headline financial facts found"}
        return {
            "ticker": ticker.upper(),
            "cik": cik,
            "entity": facts.get("entityName"),
            "source": "SEC EDGAR XBRL company facts (official annual 10-K values)",
            "note": "Official figures filed with the SEC — use to cross-check yfinance. "
                    "Each item lists the latest annual values; use --concept <Name> for full "
                    "time series, --list to see all available concepts.",
            "financials": out,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def valuation(ticker):
    try:
        df = yf.Ticker(ticker).valuation
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "error": "no valuation-measures data available"}

        def _num(v):
            # yfinance returns these as display strings; turn plain numeric
            # ones into floats but keep suffixed values like "4.54T" as text.
            v = _clean(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    return v
            return v

        periods = [str(c) for c in df.columns]
        data = {str(metric): [_num(v) for v in row.values]
                for metric, row in df.iterrows()}
        return {
            "ticker": ticker.upper(),
            "periods": periods,
            "data": data,
            "note": "Historical valuation multiples. First column 'Current' is live; "
                    "others are quarter-ends. Metrics include P/E, Forward P/E, PEG, "
                    "P/S, P/B, EV/Revenue, EV/EBITDA.",
        }
    except AttributeError:
        return {"ticker": ticker.upper(),
                "error": "valuation measures require yfinance >= 1.4.0"}
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def shares(ticker):
    try:
        s = yf.Ticker(ticker).get_shares_full()
        if s is None or len(s) == 0:
            return {"ticker": ticker.upper(), "error": "no shares-outstanding history available"}

        s = s.sort_index()
        # Collapse runs of identical values so the history shows only the
        # change points (each buyback/issuance step), not daily duplicates.
        history = []
        prev = None
        for idx, v in s.items():
            iv = int(v)
            if iv != prev:
                history.append({"date": idx.isoformat(), "shares": iv})
                prev = iv

        first = int(s.iloc[0])
        last = int(s.iloc[-1])
        change = last - first
        return {
            "ticker": ticker.upper(),
            "as_of": s.index[-1].isoformat(),
            "shares_outstanding": last,
            "earliest": {"date": s.index[0].isoformat(), "shares": first},
            "change_vs_earliest": change,
            "change_pct": _round(change / first * 100) if first else None,
            "observations": len(s),
            "change_points": len(history),
            "note": "Falling share count = buybacks; rising = issuance/dilution. "
                    "history lists only dates where the count changed.",
            "history": history,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def sec_filings(ticker, limit=20):
    try:
        filings = yf.Ticker(ticker).sec_filings or []
        out = []
        for f in filings[:limit]:
            d = f.get("date")
            out.append({
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "type": f.get("type"),
                "title": f.get("title"),
                "edgar_url": f.get("edgarUrl"),
                "exhibits": f.get("exhibits") or {},
            })
        if not out:
            return {"ticker": ticker.upper(), "filings": [], "note": "no SEC filings available"}
        return {
            "ticker": ticker.upper(),
            "count": len(out),
            "total_available": len(filings),
            "filings": out,
            "note": "type 10-K=annual report, 10-Q=quarterly, 8-K=material event. exhibits maps form name -> document URL.",
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": _format_error(e)}


def search(query, limit=10):
    try:
        res = yf.Search(query, max_results=limit)
        quotes = res.quotes or []
        out = []
        for q in quotes[:limit]:
            out.append({
                "symbol": q.get("symbol"),
                "name": q.get("longname") or q.get("shortname"),
                "type": q.get("quoteType"),
                "exchange": q.get("exchDisp") or q.get("exchange"),
                "sector": q.get("sectorDisp"),
                "industry": q.get("industryDisp"),
            })
        return {"query": query, "count": len(out), "results": out}
    except Exception as e:
        return {"query": query, "error": _format_error(e)}


SCREEN_OPS = ("eq", "gt", "lt", "gte", "lte", "btwn", "is-in")


def _screen_quote_row(q):
    return {
        "symbol": q.get("symbol"),
        "name": q.get("shortName") or q.get("longName") or q.get("displayName"),
        "price": _clean(q.get("regularMarketPrice")),
        "change_pct": _round(q.get("regularMarketChangePercent")) if q.get("regularMarketChangePercent") is not None else None,
        "market_cap": q.get("marketCap"),
        "volume": q.get("regularMarketVolume"),
        "pe_ratio": _round(q.get("trailingPE")) if q.get("trailingPE") is not None else None,
        "exchange": q.get("exchange"),
    }


def _query_class(qtype):
    return yf.ETFQuery if qtype == "etf" else yf.EquityQuery


def _build_query(qcls, filters):
    """Turn ['FIELD OP VALUE', ...] into an AND of query conditions."""
    def coerce(x):
        try:
            return float(x)
        except ValueError:
            return x

    conds = []
    for f in filters:
        parts = f.split()
        if len(parts) < 3:
            raise ValueError(f"bad filter '{f}' — expected 'FIELD OP VALUE'")
        field, op, raw = parts[0], parts[1].lower(), [coerce(v) for v in parts[2:]]
        if op not in SCREEN_OPS:
            raise ValueError(f"bad operator '{op}' in '{f}' (use {'/'.join(SCREEN_OPS)})")
        if op == "btwn":
            if len(raw) != 2:
                raise ValueError(f"'btwn' needs two values in '{f}'")
            conds.append(qcls("btwn", [field, raw[0], raw[1]]))
        elif op == "is-in":
            conds.append(qcls("is-in", [field] + raw))
        else:
            conds.append(qcls(op, [field, raw[0]]))
    if not conds:
        raise ValueError("no filters provided")
    return conds[0] if len(conds) == 1 else qcls("and", conds)


def screen(query="day_gainers", limit=25, custom=False, qtype="equity", filters=None, fields=False):
    try:
        qcls = _query_class(qtype)

        # Mode 1: list filterable fields (and allowed values for eq fields).
        if fields:
            probe_field = "fundnetassets" if qtype == "etf" else "intradaymarketcap"
            probe = qcls("gt", [probe_field, 1])
            return {
                "type": qtype,
                "valid_fields": {cat: list(fs) for cat, fs in probe.valid_fields.items()},
                "eq_field_values": {k: list(v) for k, v in probe.valid_values.items()},
                "note": "Use these with: screen --custom --type %s --filter 'FIELD OP VALUE'. "
                        "Operators: %s. eq fields (region/sector/...) take values from eq_field_values."
                        % (qtype, "/".join(SCREEN_OPS)),
            }

        # Mode 2: custom query built from --filter expressions.
        if custom:
            if not filters:
                return {"error": "custom screen needs at least one --filter 'FIELD OP VALUE' "
                                 "(run `screen --fields` to discover fields)"}
            q = _build_query(qcls, filters)
            res = yf.screen(q, size=limit)
            quotes = res.get("quotes", []) if isinstance(res, dict) else []
            return {
                "mode": "custom",
                "type": qtype,
                "filters": filters,
                "count": len(quotes[:limit]),
                "total_matches": res.get("total") if isinstance(res, dict) else None,
                "results": [_screen_quote_row(x) for x in quotes[:limit]],
            }

        # Mode 3: predefined screener (default).
        from yfinance import PREDEFINED_SCREENER_QUERIES
        available = list(PREDEFINED_SCREENER_QUERIES.keys())
        if query not in available:
            return {"error": f"unknown screener '{query}'", "available_screeners": available}

        res = yf.screen(query, size=limit)
        quotes = res.get("quotes", []) if isinstance(res, dict) else []
        return {
            "screener": query,
            "title": res.get("title") if isinstance(res, dict) else None,
            "count": len(quotes[:limit]),
            "total_matches": res.get("total") if isinstance(res, dict) else None,
            "results": [_screen_quote_row(x) for x in quotes[:limit]],
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"screener": query, "error": _format_error(e)}


VALID_SECTORS = (
    "basic-materials", "communication-services", "consumer-cyclical",
    "consumer-defensive", "energy", "financial-services", "healthcare",
    "industrials", "real-estate", "technology", "utilities",
)


def sector(name):
    try:
        key = name.strip().lower().replace(" ", "-").replace("_", "-")
        if key not in VALID_SECTORS:
            return {"sector": name, "error": f"unknown sector '{name}'",
                    "valid_sectors": list(VALID_SECTORS)}
        s = yf.Sector(key)
        ov = s.overview or {}
        out = {
            "sector": s.name,
            "key": key,
            "overview": {
                "market_cap": _clean(ov.get("market_cap")),
                "market_weight": _clean(ov.get("market_weight")),
                "companies_count": _clean(ov.get("companies_count")),
                "industries_count": _clean(ov.get("industries_count")),
                "employee_count": _clean(ov.get("employee_count")),
            },
        }
        try:
            tc = s.top_companies
            if tc is not None and not tc.empty:
                out["top_companies"] = [
                    {"symbol": idx, "name": row.get("name"),
                     "market_weight": _clean(row.get("market weight")),
                     "rating": row.get("rating")}
                    for idx, row in tc.head(15).iterrows()
                ]
        except Exception:
            pass
        try:
            te = s.top_etfs
            if te:
                out["top_etfs"] = [{"symbol": k, "name": v} for k, v in list(te.items())[:10]]
        except Exception:
            pass
        return out
    except Exception as e:
        return {"sector": name, "error": _format_error(e)}


def market(region="US"):
    try:
        valid = [r for r in dir(yf.MarketRegion) if r.isupper() and not r.startswith("_")]
        if region.upper() not in valid:
            return {"region": region, "error": f"unknown region '{region}'",
                    "valid_regions": valid}
        m = yf.Market(region.upper())
        st = m.status or {}
        if not st.get("status"):
            return {"region": region.upper(),
                    "note": "Yahoo's market-status endpoint currently only returns data for "
                            "region US; other regions report no status.",
                    "status": None}
        return {
            "region": region.upper(),
            "name": st.get("name"),
            "status": st.get("status"),
            "close": st.get("close"),
            "message": st.get("message"),
            "timezone": st.get("timezone") or st.get("tz"),
        }
    except Exception as e:
        return {"region": region, "error": _format_error(e)}


def main():
    parser = argparse.ArgumentParser(description="US stock data via yfinance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_quote = sub.add_parser("quote", help="Current quote(s)")
    p_quote.add_argument("tickers", nargs="+", help="One or more tickers (AAPL MSFT ...)")
    p_quote.add_argument("--no-cache", action="store_true",
                         help="Bypass the 5-minute cache and fetch fresh")

    p_info = sub.add_parser("info", help="Company info and financial metrics")
    p_info.add_argument("ticker")
    p_info.add_argument("--no-cache", action="store_true",
                        help="Bypass the 5-minute cache and fetch fresh")

    p_hist = sub.add_parser("history", help="Historical OHLCV data")
    p_hist.add_argument("ticker")
    p_hist.add_argument("--period", default=None,
                        help="1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max")
    p_hist.add_argument("--start", default=None, help="YYYY-MM-DD")
    p_hist.add_argument("--end", default=None, help="YYYY-MM-DD")
    p_hist.add_argument("--interval", default="1d",
                        help="1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo")
    p_hist.add_argument("--no-cache", action="store_true",
                        help="Bypass the 15-minute history cache and fetch fresh")

    p_div = sub.add_parser("dividends", help="Full dividend history")
    p_div.add_argument("ticker")

    p_split = sub.add_parser("splits", help="Stock split history")
    p_split.add_argument("ticker")

    p_earn = sub.add_parser("earnings", help="Earnings dates with EPS estimate/reported/surprise")
    p_earn.add_argument("ticker")
    p_earn.add_argument("--limit", type=int, default=12, help="Max number of earnings to return (default 12)")

    p_fin = sub.add_parser("financials", help="Income statement, balance sheet, or cash flow")
    p_fin.add_argument("ticker")
    p_fin.add_argument("--statement", choices=["income", "balance", "cashflow"], default="income",
                       help="Which statement (default: income)")
    p_fin_period = p_fin.add_mutually_exclusive_group()
    p_fin_period.add_argument("--quarterly", action="store_true",
                              help="Use quarterly data instead of annual")
    p_fin_period.add_argument("--ttm", action="store_true",
                              help="Use trailing-twelve-months data (income/cashflow only)")
    p_fin.add_argument("--no-cache", action="store_true",
                       help="Bypass the 1-hour financials cache and fetch fresh")

    p_rec = sub.add_parser("recommendations", help="Analyst recommendation counts (strongBuy/buy/hold/sell/strongSell)")
    p_rec.add_argument("ticker")

    p_cmp = sub.add_parser("compare", help="Side-by-side fundamentals for multiple tickers")
    p_cmp.add_argument("tickers", nargs="+")

    p_ret = sub.add_parser("returns", help="Returns over multiple horizons (1d/1w/1mo/3mo/6mo/ytd/1y/3y/5y/10y)")
    p_ret.add_argument("ticker")
    p_ret.add_argument("--vs", default=None,
                       help="Benchmark ticker (e.g. SPY, QQQ) — adds benchmark returns and excess (alpha) per horizon")
    p_ret.add_argument("--no-cache", action="store_true",
                       help="Bypass the 1-hour returns cache and fetch fresh")

    p_ind = sub.add_parser("indicators", help="Technical indicators (SMA, EMA, RSI, MACD, Bollinger)")
    p_ind.add_argument("ticker")
    p_ind.add_argument("--period", default="1y", help="Lookback window (default 1y; needs >=200 days for SMA-200)")

    p_vol = sub.add_parser("volatility", help="Annualized vol, max drawdown, Sharpe ratio")
    p_vol.add_argument("ticker")
    p_vol.add_argument("--period", default="1y")
    p_vol.add_argument("--rf", type=float, default=0.04,
                       help="Risk-free rate for Sharpe (default 0.04 = 4%%)")

    p_corr = sub.add_parser("correlation", help="Correlation matrix of daily returns across tickers")
    p_corr.add_argument("tickers", nargs="+")
    p_corr.add_argument("--period", default="1y")

    p_news = sub.add_parser("news", help="Recent news headlines for a ticker")
    p_news.add_argument("ticker")
    p_news.add_argument("--limit", type=int, default=10)

    p_watch = sub.add_parser("watchlist",
                             help="Manage watchlist (show by default). Persists to ~/.stock-prices/watchlist.json")
    p_watch.add_argument("action", nargs="?",
                         choices=["show", "add", "remove", "clear"], default="show")
    p_watch.add_argument("tickers", nargs="*", help="Tickers for add/remove")

    p_pf = sub.add_parser("portfolio",
                          help="Track holdings with cost basis and P/L. Persists to ~/.stock-prices/portfolio.json")
    p_pf.add_argument("action", nargs="?",
                      choices=["show", "add", "remove", "clear"], default="show")
    p_pf.add_argument("ticker", nargs="?", help="Ticker for add/remove")
    p_pf.add_argument("shares", nargs="?", type=float, help="Share count for add")
    p_pf.add_argument("--cost", type=float, default=None,
                      help="Cost basis per share (for add). Optional, but P/L only shown if set.")

    p_chart = sub.add_parser("chart",
                             help="Generate PNG chart. Single ticker = price + optional MAs. Multiple = normalized comparison.")
    p_chart.add_argument("tickers", nargs="+")
    p_chart.add_argument("--period", default="1y", help="Lookback window (default 1y)")
    p_chart.add_argument("--ma", type=lambda s: [int(x) for x in s.split(",")],
                         default=None,
                         help="Comma-separated moving average windows for single-ticker chart, e.g. 20,50,200")
    p_chart.add_argument("--out", default=None,
                         help="Output PNG path (default: ~/.stock-prices/charts/<auto>.png)")

    p_cache = sub.add_parser("cache",
                             help="Manage the quote/info cache (~/.stock-prices/cache/)")
    p_cache.add_argument("action", nargs="?", choices=["show", "clear"], default="show")

    p_etf = sub.add_parser("etf",
                           help="ETF / mutual fund data: top holdings, sector weights, expense ratio, AUM")
    p_etf.add_argument("ticker")

    p_ins = sub.add_parser("insiders",
                           help="Insider transactions (buys/sells by officers, directors)")
    p_ins.add_argument("ticker")
    p_ins.add_argument("--limit", type=int, default=20)

    p_opt = sub.add_parser("options",
                           help="Option chain (calls/puts) for nearest or given expiry")
    p_opt.add_argument("ticker")
    p_opt.add_argument("--expiry", default=None, help="YYYY-MM-DD (default: nearest). Omit to see available expiries in output.")
    p_opt.add_argument("--limit", type=int, default=40, help="Max contracts per side (default 40)")

    p_est = sub.add_parser("estimates",
                           help="Forward analyst estimates: EPS/revenue, estimate trend & revisions, growth")
    p_est.add_argument("ticker")

    p_cal = sub.add_parser("calendar",
                           help="Upcoming events: next earnings date, ex-dividend, dividend date, estimate ranges")
    p_cal.add_argument("ticker")

    p_rat = sub.add_parser("ratings",
                           help="Analyst upgrade/downgrade history (firm, from->to grade, price target)")
    p_rat.add_argument("ticker")
    p_rat.add_argument("--limit", type=int, default=25)

    p_hld = sub.add_parser("holders",
                           help="Ownership: institutional, mutual fund, major, and insider roster")
    p_hld.add_argument("ticker")
    p_hld.add_argument("--limit", type=int, default=10)

    p_srch = sub.add_parser("search",
                            help="Resolve a company name to ticker symbol(s)")
    p_srch.add_argument("query", nargs="+", help="Company name or partial symbol")
    p_srch.add_argument("--limit", type=int, default=10)

    p_scr = sub.add_parser("screen",
                           help="Stock/ETF screener: predefined, custom filters, or field discovery")
    p_scr.add_argument("query", nargs="?", default="day_gainers",
                       help="Predefined screener name (omit -> day_gainers; invalid name lists all)")
    p_scr.add_argument("--limit", type=int, default=25)
    p_scr.add_argument("--custom", action="store_true",
                       help="Build a custom screen from --filter expressions instead of a predefined name")
    p_scr.add_argument("--type", dest="qtype", choices=["equity", "etf"], default="equity",
                       help="Query universe for --custom/--fields (default equity)")
    p_scr.add_argument("--filter", dest="filters", action="append", default=None,
                       help="Custom condition 'FIELD OP VALUE' (repeatable, AND-combined). "
                            "OP: eq/gt/lt/gte/lte/btwn/is-in. e.g. --filter 'region eq us' "
                            "--filter 'intradaymarketcap gt 10000000000'")
    p_scr.add_argument("--fields", action="store_true",
                       help="List filterable fields and allowed eq-values for the chosen --type")

    p_sec = sub.add_parser("sector",
                           help="Sector overview: market cap/weight, top companies, top ETFs")
    p_sec.add_argument("name", nargs="+",
                       help="Sector (technology, healthcare, financial-services, energy, ...)")

    p_mkt = sub.add_parser("market",
                           help="Market open/closed status for a region")
    p_mkt.add_argument("region", nargs="?", default="US", help="Region code (default US)")

    p_sec_f = sub.add_parser("sec-filings",
                             help="Recent SEC filings (10-K, 10-Q, 8-K, ...) with EDGAR/document URLs")
    p_sec_f.add_argument("ticker")
    p_sec_f.add_argument("--limit", type=int, default=20)

    p_shr = sub.add_parser("shares",
                           help="Shares-outstanding over time (detects buybacks vs. dilution)")
    p_shr.add_argument("ticker")

    p_val = sub.add_parser("valuation",
                           help="Historical valuation multiples (P/E, P/S, P/B, EV/EBITDA over recent quarters)")
    p_val.add_argument("ticker")

    p_edg = sub.add_parser("edgar",
                           help="Official financials straight from SEC EDGAR XBRL filings (cross-check yfinance)")
    p_edg.add_argument("ticker")
    p_edg.add_argument("--concept", default=None,
                       help="Single XBRL concept time series, e.g. NetIncomeLoss, Revenues, Assets")
    p_edg.add_argument("--list", dest="list_concepts", action="store_true",
                       help="List all XBRL concept names available for this filer")

    args = parser.parse_args()

    if args.cmd == "quote":
        result = quote(args.tickers, use_cache=not args.no_cache)
    elif args.cmd == "info":
        result = info(args.ticker, use_cache=not args.no_cache)
    elif args.cmd == "history":
        fn = lambda: history(args.ticker, period=args.period,
                             start=args.start, end=args.end, interval=args.interval)
        key = f"hist_{args.ticker.upper()}_{args.period}_{args.start}_{args.end}_{args.interval}"
        result = fn() if args.no_cache else _cached(key, CACHE_TTL_HISTORY, fn)
    elif args.cmd == "dividends":
        result = dividends(args.ticker)
    elif args.cmd == "splits":
        result = splits(args.ticker)
    elif args.cmd == "earnings":
        result = earnings(args.ticker, limit=args.limit)
    elif args.cmd == "financials":
        fn = lambda: financials(args.ticker, statement=args.statement,
                                quarterly=args.quarterly, ttm=args.ttm)
        period = "ttm" if args.ttm else ("q" if args.quarterly else "a")
        key = f"fin_{args.ticker.upper()}_{args.statement}_{period}"
        result = fn() if args.no_cache else _cached(key, CACHE_TTL_FUNDAMENTALS, fn)
    elif args.cmd == "recommendations":
        result = recommendations(args.ticker)
    elif args.cmd == "compare":
        result = compare(args.tickers)
    elif args.cmd == "returns":
        fn = lambda: returns(args.ticker, vs=args.vs)
        key = f"returns_{args.ticker.upper()}_vs_{(args.vs or 'none').upper()}"
        result = fn() if args.no_cache else _cached(key, CACHE_TTL_FUNDAMENTALS, fn)
    elif args.cmd == "indicators":
        result = indicators(args.ticker, period=args.period)
    elif args.cmd == "volatility":
        result = volatility(args.ticker, period=args.period, risk_free_rate=args.rf)
    elif args.cmd == "correlation":
        result = correlation(args.tickers, period=args.period)
    elif args.cmd == "news":
        result = news(args.ticker, limit=args.limit)
    elif args.cmd == "watchlist":
        result = watchlist(action=args.action, tickers=args.tickers)
    elif args.cmd == "portfolio":
        result = portfolio(action=args.action, ticker=args.ticker,
                           shares=args.shares, cost=args.cost)
    elif args.cmd == "chart":
        result = chart(args.tickers, period=args.period, ma=args.ma, out=args.out)
    elif args.cmd == "cache":
        result = cache_cmd(action=args.action)
    elif args.cmd == "etf":
        result = etf(args.ticker)
    elif args.cmd == "insiders":
        result = insiders(args.ticker, limit=args.limit)
    elif args.cmd == "options":
        result = options(args.ticker, expiry=args.expiry, limit=args.limit)
    elif args.cmd == "estimates":
        result = estimates(args.ticker)
    elif args.cmd == "calendar":
        result = calendar_events(args.ticker)
    elif args.cmd == "ratings":
        result = ratings(args.ticker, limit=args.limit)
    elif args.cmd == "holders":
        result = holders(args.ticker, limit=args.limit)
    elif args.cmd == "search":
        result = search(" ".join(args.query), limit=args.limit)
    elif args.cmd == "screen":
        result = screen(args.query, limit=args.limit, custom=args.custom,
                        qtype=args.qtype, filters=args.filters, fields=args.fields)
    elif args.cmd == "sector":
        result = sector(" ".join(args.name))
    elif args.cmd == "market":
        result = market(region=args.region)
    elif args.cmd == "sec-filings":
        result = sec_filings(args.ticker, limit=args.limit)
    elif args.cmd == "shares":
        result = shares(args.ticker)
    elif args.cmd == "valuation":
        result = valuation(args.ticker)
    elif args.cmd == "edgar":
        result = edgar(args.ticker, concept=args.concept, list_concepts=args.list_concepts)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
