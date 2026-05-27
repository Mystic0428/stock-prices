# stock-prices

A Claude skill that fetches accurate US stock data from Yahoo Finance, so Claude stops making up prices from memory.

Works with **Claude Code** (local CLI) and **Claude.ai** (web, via Code execution).

## What it does

Gives Claude 36 commands to call when you ask about stocks.

**Raw data:**

- **`quote`** — current price, change, volume, market cap (single or batch)
- **`info`** — 60+ fundamentals: P/E, P/S, EBITDA, gross/operating margins, free cash flow, debt ratios, analyst targets, insider/institutional ownership, short interest, business summary
- **`valuation`** — historical valuation multiples (P/E, P/S, P/B, EV/EBITDA) across recent quarters
- **`history`** — OHLCV bars for any period and interval
- **`dividends`** — full dividend payment history
- **`splits`** — stock split events
- **`earnings`** — earnings dates with EPS estimate, reported, and surprise %
- **`financials`** — income statement, balance sheet, or cash flow (annual, quarterly, or TTM)
- **`recommendations`** — analyst buy/hold/sell counts for the last 4 months
- **`estimates`** — forward analyst estimates (EPS/revenue forecasts, estimate trend & revisions, growth)
- **`ratings`** — analyst upgrade/downgrade history (firm, grade change, price target)
- **`calendar`** — upcoming events: next earnings date, ex-dividend, dividend date, estimate ranges
- **`options`** — option chain (calls/puts, strikes, implied volatility, open interest)
- **`holders`** — ownership: institutional, mutual fund, major, and insider roster
- **`shares`** — shares outstanding over time (buyback vs. dilution)
- **`sec-filings`** — recent SEC filings (10-K, 10-Q, 8-K) with document links
- **`edgar`** — official financials straight from SEC EDGAR XBRL filings (no API key; cross-checks yfinance, independent data source)
- **`filing-text`** — narrative text from a 10-K/10-Q (MD&A, outlook, results discussion) for Claude to analyze — not just numbers (no API key)
- **`news`** — news headlines: recent (yfinance) or historical by date range (GDELT, keyless) for "why did it move then" questions
- **`etf`** — ETF holdings, sector weights, expense ratio, AUM, capital gains (for SPY, QQQ, etc.)
- **`insiders`** — officer/director buys & sells over the last 6 months

**Discovery / market-wide:**

- **`search`** — resolve a company name to ticker symbol(s)
- **`screen`** — predefined screeners (day gainers, undervalued, …) or custom equity/ETF filters by market cap, sector, P/E, etc.
- **`sector`** — sector overview: market cap/weight, top companies, top ETFs
- **`industry`** — industry/sub-sector overview + top companies (aerospace-defense, semiconductors, biotech, ... 145 industries)
- **`market`** — market open/closed status for a region
- **`fred`** — macroeconomic data from the St. Louis Fed: interest rates, CPI, unemployment, GDP, yield curve, VIX (no API key)

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
- **`cache`** — manage the result cache (quote/info 5 min, history 15 min, financials/returns 1 hour)

All output is JSON. Errors return `{"error": "..."}` so Claude can tell you what went wrong instead of guessing.

> Note: stateful features (`watchlist`, `portfolio`, `cache`, `chart`) work in **Claude Code** (local). On **Claude.ai web**, the sandbox is rebuilt each session so state does not persist.

## Install

> Requires **Python 3.10+** (yfinance 1.4.0 depends on curl_cffi ≥ 0.15, which dropped 3.9).

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

⚠️ **Also add the data domains to the allowlist**, or every command will fail to fetch. In **Settings → Capabilities → Domain allowlist**, add:

- `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, `fc.yahoo.com` — for all Yahoo-backed commands
- `www.sec.gov`, `data.sec.gov` — for `edgar`
- `fred.stlouisfed.org` — for `fred`
- `api.gdeltproject.org` — for historical `news --from/--to`

No API keys are needed (EDGAR and FRED use keyless endpoints; the SEC User-Agent is built in).

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
.venv/bin/python scripts/stock.py financials AAPL --ttm
.venv/bin/python scripts/stock.py recommendations AAPL
.venv/bin/python scripts/stock.py valuation AAPL
.venv/bin/python scripts/stock.py estimates AAPL
.venv/bin/python scripts/stock.py ratings AAPL --limit 20
.venv/bin/python scripts/stock.py calendar AAPL
.venv/bin/python scripts/stock.py options AAPL --expiry 2026-06-19
.venv/bin/python scripts/stock.py holders AAPL
.venv/bin/python scripts/stock.py shares AAPL
.venv/bin/python scripts/stock.py sec-filings AAPL --limit 10
.venv/bin/python scripts/stock.py edgar AAPL
.venv/bin/python scripts/stock.py edgar AAPL --concept NetIncomeLoss
.venv/bin/python scripts/stock.py filing-text AAPL
.venv/bin/python scripts/stock.py filing-text NVDA --type 10-K

.venv/bin/python scripts/stock.py compare NVDA AMD INTC
.venv/bin/python scripts/stock.py returns AAPL
.venv/bin/python scripts/stock.py returns AAPL --vs SPY
.venv/bin/python scripts/stock.py etf SPY
.venv/bin/python scripts/stock.py insiders AAPL --limit 10
.venv/bin/python scripts/stock.py indicators AAPL --period 1y
.venv/bin/python scripts/stock.py volatility AAPL --period 1y
.venv/bin/python scripts/stock.py correlation AAPL MSFT GOOGL NVDA --period 1y

.venv/bin/python scripts/stock.py news AAPL --limit 5
.venv/bin/python scripts/stock.py news INTC --from 2024-08-01 --to 2024-08-10   # historical (GDELT)

.venv/bin/python scripts/stock.py search "apple"
.venv/bin/python scripts/stock.py screen day_gainers --limit 20
.venv/bin/python scripts/stock.py screen --fields
.venv/bin/python scripts/stock.py screen --custom --filter "region eq us" --filter "sector eq Technology" --filter "intradaymarketcap gt 10000000000"
.venv/bin/python scripts/stock.py sector technology
.venv/bin/python scripts/stock.py industry aerospace-defense
.venv/bin/python scripts/stock.py industry --list
.venv/bin/python scripts/stock.py market US

.venv/bin/python scripts/stock.py fred 10y
.venv/bin/python scripts/stock.py fred cpi --limit 6
.venv/bin/python scripts/stock.py fred --list

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

- **Data sources**: primarily Yahoo Finance (via [yfinance](https://github.com/ranaroussi/yfinance) 1.4.0) — free, no API key, but unofficial, so occasional rate limiting and some empty endpoints (ESG, capital gains) are possible. The `edgar` command uses **SEC EDGAR** (data.sec.gov) directly — official, no key, US filers only; SEC asks for a contact in the User-Agent, overridable via the `EDGAR_USER_AGENT` env var. The `fred` command uses **FRED** (St. Louis Fed) via its keyless CSV endpoint (`fred.stlouisfed.org`) — also no API key. Historical `news --from/--to` uses **GDELT** (`api.gdeltproject.org`), a keyless global news index (history to ~2017); it indexes real articles but doesn't verify their claims, so treat headlines as contemporaneous narrative and corroborate with 8-K filings / earnings.
- **Timezone**: `timestamp` fields are US Eastern (market time).
- **Non-US tickers**: Also works with exchange-suffixed tickers like `2330.TW` (Taiwan) or `0700.HK` (Hong Kong).
- **Pre/after-hours**: `quote` returns the last regular session close, not extended-hours pricing.
- **Claude.ai web users**: The sandbox blocks outbound domains by default — see the domain allowlist in [Option 3 above](#option-3-claudeai-web).

## License

MIT
