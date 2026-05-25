# stock-prices

A Claude skill that fetches accurate US stock data from Yahoo Finance, so Claude stops making up prices from memory.

Works with **Claude Code** (local CLI) and **Claude.ai** (web, via Code execution).

## What it does

Gives Claude 21 commands to call when you ask about stocks.

**Raw data:**

- **`quote`** — current price, change, volume, market cap (single or batch)
- **`info`** — 60+ fundamentals: P/E, P/S, EBITDA, gross/operating margins, free cash flow, debt ratios, analyst targets, insider/institutional ownership, short interest, business summary
- **`history`** — OHLCV bars for any period and interval
- **`dividends`** — full dividend payment history
- **`splits`** — stock split events
- **`earnings`** — earnings dates with EPS estimate, reported, and surprise %
- **`financials`** — income statement, balance sheet, or cash flow (annual or quarterly)
- **`recommendations`** — analyst buy/hold/sell counts for the last 4 months
- **`news`** — recent news headlines for a ticker
- **`etf`** — ETF holdings, sector weights, expense ratio, AUM (for SPY, QQQ, etc.)
- **`insiders`** — officer/director buys & sells over the last 6 months

**Analytics (computed locally from price data):**

- **`compare`** — side-by-side fundamentals across multiple tickers
- **`returns`** — % return over 1d / 1w / 1mo / 3mo / 6mo / YTD / 1y / 3y / 5y / 10y, optionally `--vs SPY` for benchmark + excess returns
- **`indicators`** — SMA, EMA, RSI, MACD, Bollinger Bands
- **`volatility`** — annualized vol, max drawdown, Sharpe ratio
- **`correlation`** — Pearson correlation matrix of daily returns between tickers

**Personal tracking** (state persists to `~/.stock-prices/`):

- **`watchlist`** — list of favourite tickers with live quotes
- **`portfolio`** — holdings with shares, cost basis, current value, P/L, weights
- **`chart`** — generate a PNG (price + moving averages, or normalized comparison)
- **`cache`** — manage the 5-minute quote/info cache

All output is JSON. Errors return `{"error": "..."}` so Claude can tell you what went wrong instead of guessing.

> Note: stateful features (`watchlist`, `portfolio`, `cache`, `chart`) work in **Claude Code** (local). On **Claude.ai web**, the sandbox is rebuilt each session so state does not persist.

## Install

### Option 1: git clone (most reliable)

Brings the full skill including the Python script and requirements.

```bash
git clone https://github.com/Mystic0428/stock-prices ~/.claude/skills/stock-prices
cd ~/.claude/skills/stock-prices
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### Option 2: `npx skills` (community CLI)

```bash
npx skills add Mystic0428/stock-prices -g
```

Then run the setup step (creates `.venv` and installs `yfinance`):

```bash
cd ~/.claude/skills/stock-prices
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### Option 3: Claude.ai web

```bash
zip -r stock-prices.zip stock-prices -x '*/.venv/*' '*/__pycache__/*'
```

Upload via **Settings → Capabilities → Skills**. Requires a paid plan with Code execution enabled.

## Usage

Just ask Claude things like:

> AAPL 現在多少錢?
>
> 比較 NVDA、AMD、INTC 的本益比
>
> TSLA 過去三個月的走勢

Claude will auto-call the skill and present the result.

### Direct CLI use

The script also works standalone:

```bash
.venv/bin/python scripts/stock.py quote AAPL
.venv/bin/python scripts/stock.py quote NVDA TSLA MSFT
.venv/bin/python scripts/stock.py info AAPL
.venv/bin/python scripts/stock.py history AAPL --period 1mo
.venv/bin/python scripts/stock.py history AAPL --start 2026-01-01 --end 2026-05-01 --interval 1d

.venv/bin/python scripts/stock.py dividends AAPL
.venv/bin/python scripts/stock.py splits TSLA
.venv/bin/python scripts/stock.py earnings AAPL --limit 4
.venv/bin/python scripts/stock.py financials AAPL --statement income
.venv/bin/python scripts/stock.py financials AAPL --statement cashflow --quarterly
.venv/bin/python scripts/stock.py recommendations AAPL

.venv/bin/python scripts/stock.py compare NVDA AMD INTC
.venv/bin/python scripts/stock.py returns AAPL
.venv/bin/python scripts/stock.py returns AAPL --vs SPY
.venv/bin/python scripts/stock.py etf SPY
.venv/bin/python scripts/stock.py insiders AAPL --limit 10
.venv/bin/python scripts/stock.py indicators AAPL --period 1y
.venv/bin/python scripts/stock.py volatility AAPL --period 1y
.venv/bin/python scripts/stock.py correlation AAPL MSFT GOOGL NVDA --period 1y

.venv/bin/python scripts/stock.py news AAPL --limit 5

.venv/bin/python scripts/stock.py watchlist add AAPL MSFT NVDA
.venv/bin/python scripts/stock.py watchlist

.venv/bin/python scripts/stock.py portfolio add AAPL 10 --cost 150
.venv/bin/python scripts/stock.py portfolio

.venv/bin/python scripts/stock.py chart AAPL --period 1y --ma 20,50,200
.venv/bin/python scripts/stock.py chart NVDA AMD INTC --period 6mo

.venv/bin/python scripts/stock.py cache
.venv/bin/python scripts/stock.py cache clear
```

## Notes

- **Data source**: Yahoo Finance (via [yfinance](https://github.com/ranaroussi/yfinance)). Free, no API key, but unofficial — occasional rate limiting is possible.
- **Timezone**: `timestamp` fields are US Eastern (market time).
- **Non-US tickers**: Also works with exchange-suffixed tickers like `2330.TW` (Taiwan) or `0700.HK` (Hong Kong).
- **Pre/after-hours**: `quote` returns the last regular session close, not extended-hours pricing.
- **Claude.ai web users**: The sandbox blocks Yahoo Finance domains by default. In **Settings → Capabilities → Domain allowlist**, add `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, and `fc.yahoo.com`.

## License

MIT
