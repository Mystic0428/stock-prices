#!/usr/bin/env python3
"""Stock data CLI backed by yfinance. Outputs JSON for easy LLM consumption."""
import argparse
import json
import sys

import numpy as np
import pandas as pd
import yfinance as yf


def _round(v, n=2):
    return round(float(v), n) if v is not None else None


def quote(tickers):
    results = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", auto_adjust=False)

            if hist.empty:
                results.append({"ticker": ticker.upper(), "error": "no data (ticker may be invalid)"})
                continue

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

            results.append(result)
        except Exception as e:
            results.append({"ticker": ticker.upper(), "error": str(e)})

    return results[0] if len(results) == 1 else results


def info(ticker):
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

    p_info = sub.add_parser("info", help="Company info and financial metrics")
    p_info.add_argument("ticker")

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

    args = parser.parse_args()

    if args.cmd == "quote":
        result = quote(args.tickers)
    elif args.cmd == "info":
        result = info(args.ticker)
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
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
