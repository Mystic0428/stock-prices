# stock-prices

A Claude skill that fetches accurate US stock data from Yahoo Finance, so Claude stops making up prices from memory.

Works with **Claude Code** (local CLI) and **Claude.ai** (web, via Code execution).

## What it does

Gives Claude eight commands to call when you ask about stocks:

- **`quote`** — current price, change, volume, market cap (single or batch)
- **`info`** — company details: P/E, EPS, dividend, 52-week range, business summary, etc.
- **`history`** — OHLCV bars for any period and interval
- **`dividends`** — full dividend payment history
- **`splits`** — stock split events
- **`earnings`** — earnings dates with EPS estimate, reported, and surprise %
- **`financials`** — income statement, balance sheet, or cash flow (annual or quarterly)
- **`recommendations`** — analyst buy/hold/sell counts for the last 4 months

All output is JSON. Errors return `{"error": "..."}` so Claude can tell you what went wrong instead of guessing.

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
```

## Notes

- **Data source**: Yahoo Finance (via [yfinance](https://github.com/ranaroussi/yfinance)). Free, no API key, but unofficial — occasional rate limiting is possible.
- **Timezone**: `timestamp` fields are US Eastern (market time).
- **Non-US tickers**: Also works with exchange-suffixed tickers like `2330.TW` (Taiwan) or `0700.HK` (Hong Kong).
- **Pre/after-hours**: `quote` returns the last regular session close, not extended-hours pricing.
- **Claude.ai web users**: The sandbox blocks Yahoo Finance domains by default. In **Settings → Capabilities → Domain allowlist**, add `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, and `fc.yahoo.com`.

## License

MIT
