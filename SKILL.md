---
name: stock-prices
description: Use when user asks about US stock prices, quotes, market data, historical OHLCV, company financials (P/E, EPS, EBITDA, P/S, free cash flow), dividends, splits, earnings, financial statements, analyst recommendations and price targets, ETF holdings/expense ratio, insider transactions, side-by-side comparisons, returns vs benchmark (alpha), technical indicators (RSI, MACD, SMA, Bollinger), volatility/Sharpe/max drawdown, correlation, news headlines, watchlist, portfolio with P/L, or price charts. Triggers on ticker symbols (AAPL, TSLA, NVDA, SPY), "my portfolio", "watchlist", or any US-listed equity/ETF question.
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

**News and personal tracking:**
- Recent news headlines for a ticker → `news`
- ETF/mutual fund details (holdings, sectors, expense ratio, AUM) → `etf`
- Insider transactions (officer/director buys & sells) → `insiders`
- Manage a watchlist of tickers → `watchlist`
- Track holdings with cost basis and P/L → `portfolio`
- Generate PNG chart (price + MAs, or normalized comparison) → `chart`

**Cache:** results are cached to reduce API hits — `quote`/`info` for 5 min, `history` for 15 min, `financials`/`returns` for 1 hour. Use `--no-cache` on any of these to force a fresh fetch. Manage with `cache show` / `cache clear`.

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
.venv/bin/python scripts/stock.py returns AAPL --vs SPY     # compare to benchmark
```

Returns percent return for `1d, 1w, 1mo, 3mo, 6mo, ytd, 1y, 3y, 5y, 10y` in a single call. With `--vs <ticker>`, adds the benchmark's returns and the excess return (alpha) for each horizon — useful for "did X beat the market" questions.

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

### ETF — holdings, sectors, expense ratio

```bash
.venv/bin/python scripts/stock.py etf SPY
.venv/bin/python scripts/stock.py etf QQQ
```

For ETFs and mutual funds: top holdings (symbol + weight), sector weightings, asset class breakdown, expense ratio, total assets, fund family, category. Returns an error if the ticker is a regular stock (use `info` instead).

### Insiders — officer/director transactions

```bash
.venv/bin/python scripts/stock.py insiders AAPL --limit 10
```

Returns 6-month summary (purchases vs. sales counts and net shares) plus a list of recent individual transactions (date, insider name, position, shares, USD value, description). Useful for "is management buying or selling" questions.

### News — recent headlines

```bash
.venv/bin/python scripts/stock.py news AAPL --limit 5
```

Returns title, summary, published time, provider, and URL for recent stories. Default limit 10.

### Watchlist — track favorite tickers

```bash
.venv/bin/python scripts/stock.py watchlist                    # show all + live quotes
.venv/bin/python scripts/stock.py watchlist add AAPL MSFT NVDA
.venv/bin/python scripts/stock.py watchlist remove MSFT
.venv/bin/python scripts/stock.py watchlist clear
```

State persists to `~/.stock-prices/watchlist.json`. The `show` action calls `quote` for each ticker and includes the full quote data.

### Portfolio — track holdings with cost basis and P/L

```bash
.venv/bin/python scripts/stock.py portfolio                          # show with current value, P/L, weights
.venv/bin/python scripts/stock.py portfolio add AAPL 10 --cost 150  # add or update
.venv/bin/python scripts/stock.py portfolio add TSLA 5              # add without cost (no P/L for that line)
.venv/bin/python scripts/stock.py portfolio remove AAPL
.venv/bin/python scripts/stock.py portfolio clear
```

State persists to `~/.stock-prices/portfolio.json`. `add` is an upsert (updates if ticker exists, adds if new). `cost_basis` is optional; if omitted, the position has no P/L line but still contributes to total value and weight %.

The `show` output includes per-position current value, day change, unrealized P/L (absolute and %), portfolio weight, and a `summary` block with totals.

### Chart — generate PNG

```bash
.venv/bin/python scripts/stock.py chart AAPL --period 1y --ma 20,50,200
.venv/bin/python scripts/stock.py chart NVDA AMD INTC --period 6mo     # normalized comparison
```

Returns `chart_path` pointing to a saved PNG. Single ticker = price line + optional moving averages. Multiple tickers = normalized comparison starting at 100. In Claude Code, you can show this image inline to the user.

### Cache — quote/info results cached 5 min by default

```bash
.venv/bin/python scripts/stock.py cache                # show stats
.venv/bin/python scripts/stock.py cache clear          # wipe cache
.venv/bin/python scripts/stock.py quote AAPL --no-cache  # bypass for one call
```

Reduces API hits when the same ticker is asked about multiple times in close succession. Cache lives at `~/.stock-prices/cache/`. TTLs: `quote`/`info` 5 min, `history` 15 min, `financials`/`returns` 1 hour. Use `--no-cache` on any of these if the user explicitly asks for the freshest data.

## Tests

`scripts/test_stock.py` is a stdlib `unittest` suite (offline logic + network-tolerant live smoke). Run after changing `stock.py`:

```bash
.venv/bin/python scripts/test_stock.py
```

## Output Interpretation

- `change_pct` is already a percentage (e.g. `1.18` means +1.18%). Do not multiply by 100.
- `timestamp` is in US Eastern time (market timezone). Mention the timezone when reporting to the user.
- `market_cap`, `volume`, `revenue` are raw numbers — format with thousands separators or B/M suffix for readability.
- If the response is `{"ticker": "X", "error": "..."}`, tell the user the specific reason (invalid ticker, network issue, etc.) instead of making up a price.

## Common Pitfalls

- yfinance is unofficial. If you see repeated rate-limit errors, wait a minute and retry — don't spam.
- For non-US tickers (e.g. Taiwan `2330.TW`, Hong Kong `0700.HK`), the same commands work but mention the exchange suffix.
- Pre-market / after-hours prices are NOT included in `quote`. The `price` is the last regular session close.
