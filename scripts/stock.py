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
CACHE_TTL_SECONDS = 300  # 5 minutes — appropriate for live quotes during market hours


def _round(v, n=2):
    return round(float(v), n) if v is not None else None


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
            "eps": i.get("trailingEps"),
            "forward_eps": i.get("forwardEps"),
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
            "profit_margin": i.get("profitMargins"),
            "operating_margin": i.get("operatingMargins"),
            "return_on_equity": i.get("returnOnEquity"),
            "description": i.get("longBusinessSummary"),
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


def financials(ticker, statement="income", quarterly=False):
    try:
        t = yf.Ticker(ticker)
        attr = {
            ("income", False): "income_stmt",
            ("income", True): "quarterly_income_stmt",
            ("balance", False): "balance_sheet",
            ("balance", True): "quarterly_balance_sheet",
            ("cashflow", False): "cashflow",
            ("cashflow", True): "quarterly_cashflow",
        }.get((statement, quarterly))

        if attr is None:
            return {"ticker": ticker.upper(),
                    "error": f"invalid statement '{statement}' (use income/balance/cashflow)"}

        df = getattr(t, attr)
        if df is None or df.empty:
            return {"ticker": ticker.upper(), "statement": statement,
                    "period_type": "quarterly" if quarterly else "annual",
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
            "period_type": "quarterly" if quarterly else "annual",
            "periods": periods,
            "data": data,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        rows = []
        for ticker in tickers:
            t = yf.Ticker(ticker)
            try:
                i = t.info or {}
                hist = t.history(period="ytd", auto_adjust=False)
                ytd = _ytd_return(hist["Close"]) if not hist.empty else None
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
                rows.append({"ticker": ticker.upper(), "error": str(e)})
        return {"tickers": [t.upper() for t in tickers], "count": len(rows), "comparison": rows}
    except Exception as e:
        return {"error": str(e)}


def returns(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="max", auto_adjust=False)
        if hist.empty:
            return {"ticker": ticker.upper(), "error": "no price data"}

        close = hist["Close"]
        periods = {
            "1d": 1, "1w": 7, "1mo": 30, "3mo": 91, "6mo": 182,
            "1y": 365, "3y": 365 * 3, "5y": 365 * 5, "10y": 365 * 10,
        }
        result = {p: _period_return(close, d) for p, d in periods.items()}
        result["ytd"] = _ytd_return(close)

        return {
            "ticker": ticker.upper(),
            "as_of": close.index[-1].isoformat(),
            "current_price": _round(float(close.iloc[-1])),
            "returns_pct": result,
        }
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"ticker": ticker.upper(), "error": str(e)}


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
        return {"error": str(e), "tickers": tickers}


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

        closes = {}
        for ticker in tickers:
            hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if hist.empty:
                return {"error": f"no data for {ticker.upper()}"}
            closes[ticker.upper()] = hist["Close"]

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
        return {"error": str(e)}


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
    p_fin.add_argument("--quarterly", action="store_true",
                       help="Use quarterly data instead of annual")

    p_rec = sub.add_parser("recommendations", help="Analyst recommendation counts (strongBuy/buy/hold/sell/strongSell)")
    p_rec.add_argument("ticker")

    p_cmp = sub.add_parser("compare", help="Side-by-side fundamentals for multiple tickers")
    p_cmp.add_argument("tickers", nargs="+")

    p_ret = sub.add_parser("returns", help="Returns over multiple horizons (1d/1w/1mo/3mo/6mo/ytd/1y/3y/5y/10y)")
    p_ret.add_argument("ticker")

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

    args = parser.parse_args()

    if args.cmd == "quote":
        result = quote(args.tickers, use_cache=not args.no_cache)
    elif args.cmd == "info":
        result = info(args.ticker, use_cache=not args.no_cache)
    elif args.cmd == "history":
        result = history(args.ticker, period=args.period,
                         start=args.start, end=args.end, interval=args.interval)
    elif args.cmd == "dividends":
        result = dividends(args.ticker)
    elif args.cmd == "splits":
        result = splits(args.ticker)
    elif args.cmd == "earnings":
        result = earnings(args.ticker, limit=args.limit)
    elif args.cmd == "financials":
        result = financials(args.ticker, statement=args.statement, quarterly=args.quarterly)
    elif args.cmd == "recommendations":
        result = recommendations(args.ticker)
    elif args.cmd == "compare":
        result = compare(args.tickers)
    elif args.cmd == "returns":
        result = returns(args.ticker)
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
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
