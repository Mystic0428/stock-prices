---
name: stock-prices
description: Use when user asks about US stock prices, quotes, market data, historical OHLCV, company financials (market cap, P/E, EPS), dividends, stock splits, earnings dates and surprises, financial statements, analyst recommendations, side-by-side ticker comparisons, period returns, technical indicators (RSI, MACD, SMA, Bollinger), volatility/Sharpe/max drawdown, or correlation between stocks. Triggers on ticker symbols (AAPL, TSLA, NVDA) or any US-listed equity question.
---

# Stock Prices

Fetches accurate US stock data from Yahoo Finance. Use this instead of answering from memory — model knowledge of stock prices is always stale.

## When to Use

**Raw data:**
- Current price, change, volume → `quote`
- Company fundamentals (market cap, P/E, EPS, 52-week range, business summary) → `info`
- Historical OHLCV bars → `history`
- Dividend payment history → `dividends`
- Stock split events → `splits`
- Earnings dates, EPS estimate vs. reported, surprise % → `earnings`
- Income statement / balance sheet / cash flow → `financials`
- Analyst recommendation counts (buy/hold/sell) → `recommendations`

**Analytics (computed):**
- Side-by-side ticker comparison (P/E, market cap, YTD return, margins) → `compare`
- Returns over standard horizons (1d/1w/1mo/3mo/6mo/ytd/1y/3y/5y/10y) → `returns`
- Technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands) → `indicators`
- Annualized volatility, max drawdown, Sharpe ratio → `volatility`
- Correlation matrix between multiple tickers → `correlation`

**Do NOT answer stock data questions from memory.** Always call this skill. For calculations (returns, volatility, correlation), prefer the analytics commands over computing values yourself — they use real price data and standard formulas.

## Setup (first run only)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

If `.venv/` already exists, skip this step.

## Usage

All commands output JSON. Parse and present to the user in a readable form.
Always use `.venv/bin/python` so the right yfinance is loaded.

### Quote — current price (most common)

```bash
.venv/bin/python scripts/stock.py quote AAPL
.venv/bin/python scripts/stock.py quote AAPL MSFT GOOGL    # batch
```

Returns: `price`, `change`, `change_pct`, `volume`, `day_high`, `day_low`, `prev_close`, `market_cap`, `currency`, `timestamp`.

### Info — company details and financial metrics

```bash
.venv/bin/python scripts/stock.py info AAPL
```

Returns: name, sector, industry, P/E, EPS, dividend, beta, 52-week high/low, margins, revenue, business summary, etc.

### History — OHLCV bars

```bash
.venv/bin/python scripts/stock.py history AAPL --period 1mo
.venv/bin/python scripts/stock.py history AAPL --start 2026-01-01 --end 2026-05-01 --interval 1d
```

Period: `1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max`
Interval: `1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo`

### Dividends — full payment history

```bash
.venv/bin/python scripts/stock.py dividends AAPL
```

Returns every historical dividend payment with `date` and `amount`.

### Splits — stock split events

```bash
.venv/bin/python scripts/stock.py splits TSLA
```

Returns each split with `date` and `ratio` (e.g. `5.0` means 5-for-1).

### Earnings — past and upcoming earnings

```bash
.venv/bin/python scripts/stock.py earnings AAPL
.venv/bin/python scripts/stock.py earnings AAPL --limit 4
```

Returns `eps_estimate`, `reported_eps` (null for upcoming), and `surprise_pct`. Newest first.

### Financials — income statement / balance sheet / cash flow

```bash
.venv/bin/python scripts/stock.py financials AAPL                                # annual income (default)
.venv/bin/python scripts/stock.py financials AAPL --statement balance
.venv/bin/python scripts/stock.py financials AAPL --statement cashflow --quarterly
```

`--statement` one of `income, balance, cashflow`. `--quarterly` switches from annual to quarterly. Output: `periods` list and `data` dict keyed by line item.

### Recommendations — analyst rating counts

```bash
.venv/bin/python scripts/stock.py recommendations AAPL
```

Returns counts of analysts at each rating (`strongBuy`, `buy`, `hold`, `sell`, `strongSell`) for the current month and previous 3 months. Period `0m` = current month, `-1m` = last month, etc.

### Compare — side-by-side fundamentals

```bash
.venv/bin/python scripts/stock.py compare NVDA AMD INTC
```

For each ticker: price, market cap, P/E, forward P/E, EPS, dividend yield, beta, 52-week range, YTD return, profit margin, ROE. Useful when user wants to evaluate alternatives.

### Returns — performance over multiple horizons

```bash
.venv/bin/python scripts/stock.py returns AAPL
```

Returns percent return for `1d, 1w, 1mo, 3mo, 6mo, ytd, 1y, 3y, 5y, 10y` in a single call. Saves multiple `history` calls when the user asks "how did X do over different periods".

### Indicators — technical analysis

```bash
.venv/bin/python scripts/stock.py indicators AAPL --period 1y
```

Returns SMA (20/50/200), EMA (12/26), RSI-14, MACD (line/signal/histogram), and Bollinger Bands (20/2). Output includes `interpretation_hints` so you know how to read RSI/MACD/Bollinger %B. Needs ≥200 trading days for SMA-200, so `--period 1y` is the practical minimum.

### Volatility — risk metrics

```bash
.venv/bin/python scripts/stock.py volatility AAPL --period 1y
.venv/bin/python scripts/stock.py volatility AAPL --period 5y --rf 0.045
```

Returns annualized volatility, annualized return, max drawdown, and Sharpe ratio. Default risk-free rate is 4%; override with `--rf` if user specifies a different rate.

### Correlation — relationship between stocks

```bash
.venv/bin/python scripts/stock.py correlation AAPL MSFT GOOGL NVDA --period 1y
```

Returns Pearson correlation matrix of daily returns. Useful for portfolio diversification questions ("are these too correlated?"). Needs ≥30 overlapping trading days.

## Output Interpretation

- `change_pct` is already a percentage (e.g. `1.18` means +1.18%). Do not multiply by 100.
- `timestamp` is in US Eastern time (market timezone). Mention the timezone when reporting to the user.
- `market_cap`, `volume`, `revenue` are raw numbers — format with thousands separators or B/M suffix for readability.
- If the response is `{"ticker": "X", "error": "..."}`, tell the user the specific reason (invalid ticker, network issue, etc.) instead of making up a price.

## Common Pitfalls

- yfinance is unofficial. If you see repeated rate-limit errors, wait a minute and retry — don't spam.
- For non-US tickers (e.g. Taiwan `2330.TW`, Hong Kong `0700.HK`), the same commands work but mention the exchange suffix.
- Pre-market / after-hours prices are NOT included in `quote`. The `price` is the last regular session close.
