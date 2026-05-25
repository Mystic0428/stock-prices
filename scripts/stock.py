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

    args = parser.parse_args()

    if args.cmd == "quote":
        result = quote(args.tickers)
    elif args.cmd == "info":
        result = info(args.ticker)
    elif args.cmd == "history":
        result = history(args.ticker, period=args.period,
                         start=args.start, end=args.end, interval=args.interval)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
