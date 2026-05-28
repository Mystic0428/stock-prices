---
name: stock-prices
description: Use for US stock/ETF questions — prices, quotes, OHLCV history; fundamentals (P/E, EPS, EBITDA, margins, free cash flow) and valuation multiples over time; financial statements (annual/quarterly/TTM); dividends, splits, earnings, SEC filings; analyst recommendations, price targets, forward estimates, upgrade/downgrade history, earnings calendar; option chains and implied volatility; institutional/fund/insider ownership and transactions; ETF holdings/expense ratio; shares outstanding (buybacks); comparisons, returns vs benchmark (alpha), technical indicators (RSI/MACD/SMA/Bollinger), volatility/Sharpe/drawdown, correlation; news, watchlist, portfolio P/L, price charts; resolving a company name to a ticker; stock/ETF screeners (gainers, undervalued, or custom filters by market cap/sector/P/E); sector overviews; market status; macro data (rates, CPI, GDP) via FRED. Triggers on ticker symbols (AAPL, TSLA, NVDA, SPY), company names to resolve, "my portfolio", "watchlist", or any US-listed equity/ETF question.
---

# Stock Prices

Fetches accurate US stock data from Yahoo Finance. Use this instead of answering from memory — model knowledge of stock prices is always stale.

## When to Use

**Raw data:**
- Current price, change, volume → `quote`
- Company fundamentals (market cap, P/E, EPS, 52-week range, business summary) → `info`
- Historical valuation multiples (P/E, P/S, P/B, EV/EBITDA over recent quarters) → `valuation`
- Historical OHLCV bars → `history`
- Dividend payment history → `dividends`
- Stock split events → `splits`
- Earnings dates, EPS estimate vs. reported, surprise % → `earnings`
- Income statement / balance sheet / cash flow (annual, quarterly, or TTM) → `financials`
- SEC filings (10-K, 10-Q, 8-K) with document links → `sec-filings`
- Official financials straight from SEC EDGAR XBRL (cross-check yfinance) → `edgar`
- Narrative filing content — management's discussion (MD&A), outlook, results drivers → `filing-text`
- Shares-outstanding over time / buyback detection → `shares`
- Analyst recommendation counts (buy/hold/sell) → `recommendations`
- Forward analyst estimates (EPS/revenue forecasts, estimate trend & revisions, growth) → `estimates`
- Analyst upgrade/downgrade history (firm, grade change, price target) → `ratings`
- Upcoming events (next earnings date, ex-dividend, dividend date, estimate ranges) → `calendar`
- Option chains (calls/puts, strikes, implied volatility, open interest) → `options`
- Ownership breakdown (institutional, mutual fund, major, insider roster) → `holders`

**Discovery / market-level:**
- Resolve a company name to ticker symbol → `search`
- Predefined stock screeners (day gainers/losers, undervalued, most active, ...) → `screen`
- Sector overview (market cap/weight, top companies, top ETFs) → `sector`
- Industry / sub-sector overview + leaders (aerospace-defense, semiconductors, biotech, ...) → `industry`
- Market open/closed status for a region → `market`
- Macroeconomic data — interest rates, CPI, unemployment, GDP, yield curve → `fred`

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

Requires **Python 3.10+** (yfinance 1.4.0 depends on curl_cffi ≥ 0.15, which dropped 3.9).

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

Returns: name, sector, industry, P/E, EPS, dividend, beta, 52-week high/low, margins, revenue, business summary, plus a `_units` block that documents each field's time-scope (TTM / quarterly-YoY / point-in-time / forward).

**⚠️ Common mis-reads — yfinance mixes scopes under similar-sounding names:**

| Field | Actual scope | Common wrong assumption |
|---|---|---|
| `revenue_growth` | **MOST RECENT QUARTER YoY** | "FY annual YoY" or "TTM YoY" |
| `earnings_growth` / `earnings_quarterly_growth` | most recent quarter YoY | same |
| `revenue`, `ebitda`, `free_cashflow`, `operating_cashflow` | **TTM** (last 4 reported quarters) | "latest annual" or "current run rate" |
| All margins (`profit_margin`, `gross_margin`, `operating_margin`, `ebitda_margin`) | TTM | "latest quarter" |
| `total_cash`, `total_debt`, `debt_to_equity`, `book_value_per_share` | most-recent-quarter balance sheet | "current" |
| `forward_pe`, `forward_eps`, `analyst_target_*` | forward consensus | TTM |

**Why this matters:** for a company with a recent inflection (e.g. revenue went `+10%, +15%, +20%, +44%` over four quarters), `revenue_growth` reports **+44% (the last QoQ-YoY)** — not the +20% TTM average nor the +163% FY-vs-prior-FY. Always confirm direction with `financials --statement income` (annual + quarterly) before drawing conclusions on growth trajectory or inflection.

Always read the inline `_units` block in the response when reporting a metric to the user.

### Valuation — historical multiples

```bash
.venv/bin/python scripts/stock.py valuation AAPL
```

Returns a `periods` list (`Current` plus recent quarter-ends) and `data` keyed by metric: Market Cap, Enterprise Value, Trailing/Forward P/E, PEG, Price/Sales, Price/Book, EV/Revenue, EV/EBITDA. Ratio values are numbers; large totals like Market Cap stay as display strings (`"4.54T"`). Use to see whether a stock is getting cheaper or more expensive over time, vs. `info` which is a single point in time.

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
.venv/bin/python scripts/stock.py financials AAPL --quarterly --quarters 8        # cap to N most recent
.venv/bin/python scripts/stock.py financials AAPL --ttm                           # trailing twelve months
```

`--statement` one of `income, balance, cashflow`. Period: default annual, or `--quarterly`, or `--ttm` (trailing twelve months; mutually exclusive with `--quarterly`). TTM is only available for income and cash flow — `--ttm --statement balance` returns an error since balance sheets are point-in-time. Output: `period_type`, `periods` list, and `data` dict keyed by line item.

`--quarters N` works with `--quarterly` and caps the output to the N most-recent quarters. **yfinance typically only returns ~4-5 quarters here** — for longer quarterly trajectories (e.g. 20-quarter trend), use `edgar --concept Revenues` (or another XBRL tag), which can pull years of quarterly history straight from SEC.

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

For ETFs and mutual funds: top holdings (symbol + weight), sector weightings, asset class breakdown, expense ratio, total assets, fund family, category, and `capital_gains` distributions when the fund has them (many index funds don't, so the key is omitted in that case). Returns an error if the ticker is a regular stock (use `info` instead).

### Insiders — officer/director transactions

```bash
.venv/bin/python scripts/stock.py insiders AAPL --limit 10
```

Returns three blocks:

- `summary_6mo_yfinance` — yfinance's raw 6-month headline (purchases vs. sales counts and net shares). **Do NOT report this number to the user without checking `summary_by_form4_code` first** — yfinance counts RSU grants as "purchases" and tax withholding as "sales", which is the single most common false signal in this command.
- `summary_by_form4_code` — the same window split by SEC Form 4 transaction code, the actual unit of meaning:

  | Code | Label | What it means |
  |---|---|---|
  | **P** | `open_market_purchase` | **Real bullish signal** — insider spent cash to buy in the open market |
  | **S** | `open_market_sale` | **Real bearish signal** — insider sold for cash |
  | A | `rsu_grant` | Restricted stock awarded @ $0 — compensation, NOT conviction |
  | M | `option_exercise` | Exercised/converted derivatives — often paired with an S |
  | F | `tax_withholding` | Shares withheld to cover RSU/option tax — NOT a sell |
  | G | `gift` | Bona fide gift |

- `transactions` — every recent transaction with a `form4_code` and `form4_code_label` so you can verify the classification.

**Workflow for "is management buying?":**
1. Look at `summary_by_form4_code["P"]` first — if `count == 0`, there is **no real insider buying** regardless of what `summary_6mo_yfinance` says.
2. A cluster of paired `M` + `S` from the same insider (option exercise → immediate sale) is a *cash-out*, not a bearish signal — it's compensation realization.
3. A `A`-only summary means the company runs an RSU comp program; it's neither bullish nor bearish.

### Options — option chain

```bash
.venv/bin/python scripts/stock.py options AAPL                       # nearest expiry
.venv/bin/python scripts/stock.py options AAPL --expiry 2026-06-19   # specific expiry
.venv/bin/python scripts/stock.py options AAPL --limit 20            # cap contracts per side
```

Returns `available_expiries` plus `calls` and `puts` for the chosen expiry (strike, bid/ask, lastPrice, volume, openInterest, impliedVolatility, inTheMoney). `impliedVolatility` is a fraction (`0.25` = 25%). Omit `--expiry` to fetch the nearest; the response always lists all expiries so you can pick another.

### Estimates — forward analyst consensus

```bash
.venv/bin/python scripts/stock.py estimates AAPL
```

Returns `earnings_estimate` and `revenue_estimate` (avg/low/high/numberOfAnalysts/growth), `eps_trend` (how the estimate moved 7/30/60/90 days ago), `eps_revisions` (up/down counts), and `growth_estimates`. Periods: `0q` current quarter, `+1q` next quarter, `0y` current year, `+1y` next year. Use for "what do analysts expect next quarter" questions.

### Ratings — analyst upgrade/downgrade history

```bash
.venv/bin/python scripts/stock.py ratings AAPL --limit 20
```

Returns recent rating changes (newest first): `Firm`, `FromGrade`→`ToGrade`, `Action` (up/down/init/main/reit), and price target. Different from `recommendations` (which is current aggregate counts) — this is the time series of individual firm actions.

### Calendar — upcoming events

```bash
.venv/bin/python scripts/stock.py calendar AAPL
```

Returns next `Earnings Date`, `Ex-Dividend Date`, `Dividend Date`, and the consensus earnings/revenue estimate ranges (High/Low/Average) for the upcoming report.

### Holders — ownership breakdown

```bash
.venv/bin/python scripts/stock.py holders AAPL --limit 10
```

Returns `major_holders` (insider/institution percentages), `institutional_holders` and `mutualfund_holders` (top holders with shares, value, pctHeld, pctChange), and `insider_roster` (current officers/directors and shares owned). `pctHeld` values are fractions (`0.65` = 65%).

### Search — resolve company name to ticker

```bash
.venv/bin/python scripts/stock.py search "apple"
.venv/bin/python scripts/stock.py search tesla --limit 5
```

Returns matching symbols with name, type, exchange, sector, industry. Use this first when the user names a company but not its ticker.

### Screen — predefined stock screeners

```bash
.venv/bin/python scripts/stock.py screen day_gainers --limit 20
.venv/bin/python scripts/stock.py screen undervalued_growth_stocks
.venv/bin/python scripts/stock.py screen bogus       # invalid name lists all available screeners
```

Available screeners include `day_gainers`, `day_losers`, `most_actives`, `most_shorted_stocks`, `aggressive_small_caps`, `growth_technology_stocks`, `undervalued_growth_stocks`, `undervalued_large_caps`, `small_cap_gainers`, plus fund screeners. Each result has symbol, name, price, change %, market cap, volume, P/E. An unknown name returns the full list in `available_screeners`.

**⚠️ Preset screener algorithms are not what they sound like.** Yahoo's `undervalued_*` presets are effectively **low-trailing-P/E sorts** with light filters — they routinely surface telcos, energy majors, banks, and non-US ADRs (AT&T, Carnival, Petrobras, AGNC, Banco Santander) rather than the "undervalued growth" names a user typically means. Treat the preset list as `low_pe_screen` and **do not pass it through to the user as "undervalued growth stocks"** without a caveat. For high-conviction screens (e.g. "growing but cheap US tech"), use `--custom` with explicit filters.

**Custom screens** — build your own filters instead of a predefined list:

```bash
# discover what you can filter on (equity or etf)
.venv/bin/python scripts/stock.py screen --fields
.venv/bin/python scripts/stock.py screen --fields --type etf

# US tech stocks with market cap > $10B
.venv/bin/python scripts/stock.py screen --custom \
  --filter "region eq us" --filter "sector eq Technology" \
  --filter "intradaymarketcap gt 10000000000" --limit 10

# ETFs with net assets > $1B
.venv/bin/python scripts/stock.py screen --custom --type etf \
  --filter "fundnetassets gt 1000000000"
```

Each `--filter` is `FIELD OP VALUE`, repeatable and AND-combined. Operators: `eq, gt, lt, gte, lte, btwn` (two values), `is-in` (multiple values). Run `screen --fields` first to get exact field names (e.g. `intradaymarketcap`, `peratio.lasttwelvemonths`) and the allowed values for eq fields like `region`/`sector`. `--type` is `equity` (default) or `etf` — their field sets differ.

### Sector — sector overview

```bash
.venv/bin/python scripts/stock.py sector technology
.venv/bin/python scripts/stock.py sector "financial services"
```

Returns market cap, market weight, company/industry counts, top companies (with market weight + rating), and top ETFs. Valid sectors: `basic-materials, communication-services, consumer-cyclical, consumer-defensive, energy, financial-services, healthcare, industrials, real-estate, technology, utilities` (spaces are normalized to hyphens). An invalid name returns `valid_sectors`.

### Industry — sub-sector overview + leaders

```bash
.venv/bin/python scripts/stock.py industry aerospace-defense
.venv/bin/python scripts/stock.py industry semiconductors
.venv/bin/python scripts/stock.py industry "Aerospace & Defense"   # loose input is normalized
.venv/bin/python scripts/stock.py industry --list                  # all 145 industry keys by sector
```

The granular layer below `sector` — 145 industries like `aerospace-defense`, `semiconductors`, `biotechnology`, `oil-gas-e-p`, `software-infrastructure`, `airlines`. Returns the industry's market cap/weight, company count, top companies (the leaders/biggest names in that space), and top performers. Use for "航太板塊 / aerospace stocks", "semiconductor leaders", etc. Input is normalized (`Aerospace & Defense` → `aerospace-defense`); an unknown name returns `did_you_mean` suggestions. **Themes vs. industries:** things like "AI" are *not* formal industries — for an AI basket use `industry semiconductors` (AI chips) or a thematic ETF's holdings (`etf BOTZ`, `etf AIQ`).

**`top_companies` vs `top_performing` — completely different lists:**
- `top_companies` = ranked by market cap / industry weight → these are the **structural leaders** (e.g. LMT, RTX, NOC for aerospace-defense). Use this for "who dominates this space".
- `top_performing` = ranked by recent **price momentum** (YTD / 6mo return) → these are often small caps that just spiked, including ones with structural problems (dilution, going concern). Use this for "what's moving in the space lately", **never** for "who's the best company".

Do not conflate the two. A short-term momentum winner in `top_performing` is not evidence of fundamental strength.

### Market — open/closed status

```bash
.venv/bin/python scripts/stock.py market US
```

Returns whether the region's markets are open or closed, the next close time, and a status message. Valid regions: `US, EUROPE, ASIA, GB, COMMODITIES, CURRENCIES, CRYPTOCURRENCIES, RATES` (an invalid one returns `valid_regions`). Note: Yahoo's status endpoint currently only returns live data for `US`; other regions report no status.

### FRED — macroeconomic data

```bash
.venv/bin/python scripts/stock.py fred 10y           # 10-Year Treasury yield
.venv/bin/python scripts/stock.py fred cpi
.venv/bin/python scripts/stock.py fred unemployment --limit 6
.venv/bin/python scripts/stock.py fred --series DGS5   # any FRED series id
.venv/bin/python scripts/stock.py fred --list          # built-in series
```

Macro context from the St. Louis Fed (FRED), which yfinance has no equivalent for — interest rates, inflation, jobs, GDP, money supply, yield-curve spread, VIX, sentiment. Built-in names: `fedfunds, 3mo, 2y, 10y, 30y, yield-curve, cpi, core-cpi, core-pce, inflation-expectation, unemployment, payrolls, gdp, m2, mortgage30, vix, sentiment`. Use `--series <ID>` for any other FRED series. Returns `latest` plus `recent` observations; `units` tells you how to read the value (percent vs index vs level). **No API key needed** — uses FRED's keyless CSV endpoint.

### SEC filings — list with filters

```bash
.venv/bin/python scripts/stock.py sec-filings AAPL --limit 10
.venv/bin/python scripts/stock.py sec-filings KTOS --type 8-K --from 2026-02-01 --to 2026-03-31
.venv/bin/python scripts/stock.py sec-filings SYM --year 2026 --type 8-K --item 2.02
```

Lists filings from EDGAR (newest first): `date`, `type`, `title`, `items` (8-K), `accession`, `primary_doc_url`, `edgar_url`. Filters: `--from`/`--to`/`--year` for date range; `--type` for form types (case-insensitive, comma-separated); `--item` for 8-K Item numbers (requires `--type` containing 8-K). `--year` is mutex with `--from`/`--to`. Use the `accession` value with `filing-text --accession` to fetch a specific filing.

### EDGAR — official financials from SEC XBRL

```bash
.venv/bin/python scripts/stock.py edgar AAPL                       # headline annual financials
.venv/bin/python scripts/stock.py edgar AAPL --concept NetIncomeLoss   # full time series for one concept
.venv/bin/python scripts/stock.py edgar AAPL --list                # all available XBRL concept names
```

Pulls figures **directly from companies' SEC filings** (data.sec.gov XBRL), independent of Yahoo — use it to cross-check or when yfinance is rate-limited. Default returns the latest annual (10-K) values for revenue, gross profit, operating/net income, EPS, assets, liabilities, equity, and cash; `fiscal_year` is derived from the period-end date. `--concept <Name>` returns the annual + quarterly series for one XBRL tag (run `--list` first to see valid names; common ones: `Revenues`, `NetIncomeLoss`, `Assets`, `StockholdersEquity`). US filers only; non-US tickers return "not found". No API key needed. SEC requires a contact in the User-Agent — override the default with the `EDGAR_USER_AGENT` env var (e.g. `"yourname your@email.com"`) per SEC's fair-access policy.

**Sparse data on small caps:** EDGAR's structured XBRL coverage varies. Many small / micro-cap filers (e.g. AMSC was empty in practice) tag a minimal subset, so the default headline may return blanks. When that happens, fall back to either (a) `financials --statement income` (yfinance has broader normalized coverage for small caps), or (b) `sec-filings` + `filing-text --section mda` for the narrative numbers. Don't claim "no data exists" just because the structured query came back empty.

### Filing text — narrative + exhibits from SEC filings

```bash
.venv/bin/python scripts/stock.py filing-text NVDA --type 10-K --section mda
.venv/bin/python scripts/stock.py filing-text SYM --type 8-K --exhibit ex-99.2
.venv/bin/python scripts/stock.py filing-text KTOS --type S-3ASR --section dilution
.venv/bin/python scripts/stock.py filing-text SYM --accession 0001899830-26-000051 --list-exhibits
```

Fetches a SEC filing's text from EDGAR and optionally extracts a section.

**Types**: `10-K`, `10-Q`, `8-K`, `8-K/A`, `S-1`, `S-1/A`, `S-3`, `S-3/A`, `S-3ASR`, `424B1-5`, `424B7`, `DEF 14A`.

**Sections vary by type:**
- `10-K`: mda, business, risk, properties, legal
- `10-Q`: mda, risk
- `8-K`: no sections — use `--exhibit` for attachments (EX-99.1 = press release HTML, EX-99.2 = investor deck PDF). **Always run `--list-exhibits` first** before attempting `--exhibit ex-99.X`: 8-K attachment numbering varies (some filings have no EX-99.2, some have EX-99.3+, some have only the body). Listing first avoids "exhibit not found" errors and shows what the filing actually contains.
- `S-*` / `424B*`: summary, risk, use-of-proceeds, dilution, capitalization, underwriting, plan-of-distribution, business
- `DEF 14A`: compensation, directors, transactions

**Filing selection:** defaults to latest of `--type`. Use `--date YYYY-MM-DD` to pick a specific day (errors if multiple same-day, suggesting `--accession`), or `--accession` to pin one exactly.

**Exhibits:** `--exhibit ex-99.1` (case-insensitive); `--list-exhibits` enumerates without fetching content. PDFs are extracted via pdfplumber with page markers.

**Char limit:** defaults vary by form (50k for 8-K body up to 500k for 10-K --full). Override with `--max-chars N` (hard cap 2,000,000). Output includes `truncated` and (for PDFs) `truncated_at_page`.

**Errors:** section not found → suggests `--full` or `--list-exhibits`; exhibit not found → lists available; `--date` no match → lists nearest dates. US filers only.

### Shares — shares outstanding over time

```bash
.venv/bin/python scripts/stock.py shares AAPL
```

Returns current `shares_outstanding`, the `earliest` data point, `change_vs_earliest` and `change_pct`, plus a `history` of just the dates where the count changed. A falling count over time signals buybacks; a rising count signals issuance/dilution. Use for "is the company buying back stock" questions.

### News — recent or historical headlines

```bash
.venv/bin/python scripts/stock.py news AAPL --limit 5                       # recent (yfinance)
.venv/bin/python scripts/stock.py news INTC --from 2024-08-01 --to 2024-08-10   # historical (GDELT)
```

Without dates: recent stories from yfinance (title, summary, time, provider, URL) — recent-only and somewhat noisy. With `--from`/`--to` (YYYY-MM-DD): switches to **GDELT**, a keyless global news index with history back to ~2017, returning title, date, source domain, language, country, and URL. Use the date-range form to investigate *why a stock moved on/around a past date*.

**Accuracy:** GDELT faithfully indexes articles that really were published, but does **not** verify their claims or filter source quality — treat headlines as the contemporaneous *narrative* (including speculation), not established fact. Confirm the actual cause against the official record: `edgar`/`sec-filings` (8-K material events) and `earnings`.

**"Why did X drop?" workflow:** (1) `history` to pinpoint the drop date and size → (2) `news <ticker> --from <a few days before> --to <date>` for what was being reported → (3) `sec-filings`/`edgar` for any 8-K filed then, and `earnings` for a miss → (4) synthesize the cause from those, not from a single headline.

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
- On **Claude.ai web**, the sandbox blocks outbound network by default. If commands fail to fetch, tell the user to add the data domains to **Settings → Capabilities → Domain allowlist**: `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `finance.yahoo.com`, `fc.yahoo.com` (Yahoo-backed commands), `www.sec.gov`, `data.sec.gov` (the `edgar` command), `fred.stlouisfed.org` (the `fred` command), and `api.gdeltproject.org` (historical `news --from/--to`). EDGAR, FRED, and GDELT need no API key, so no env var is required on web.
