#!/usr/bin/env python3
"""Stock data CLI backed by yfinance. Outputs JSON for easy LLM consumption."""
import argparse
import json
import sys

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
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
