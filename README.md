# Congressional Trading Disclosure Analysis

Do members of Congress show signs of informed trading — and does *how late they file*
their disclosure tell you anything about how well the trade did?

Under the [STOCK Act](https://en.wikipedia.org/wiki/STOCK_Act), members of Congress must
disclose securities transactions within 45 days. This project builds a pipeline over those
disclosures and tests whether disclosure lag, position size, and trade direction carry any
predictive signal.

## Data

Disclosures are sourced from the [Kadoa](https://www.kadoa.com/congress) public congressional
trading dataset (no API key required).

| | |
|---|---|
| Trades ingested | 5,000 |
| Filers tracked | 435 (75 with trade activity) |
| Unique tickers | 333 |
| Date range | Jan 2025 – Jul 2026 |
| Estimated notional | ~$750M (bracket midpoints) |
| Transaction mix | 2,497 purchases · 2,262 full sales · 232 partial sales · 9 exchanges |

Market prices come from Yahoo Finance via `yfinance`, split- and dividend-adjusted.

## Architecture

```
Kadoa REST API  ──►  CongressData      ingestion + normalization
                          │
                          ▼
                     SQLite            raw_trade (26 cols), raw_filers (15 cols)
                          │
                          ▼
              CongressTradeAnalyzer    analysis + portfolio reconstruction
                          ▲
                          │
Yahoo Finance   ──►  yfinance          adjusted prices, batched
```

**`congress_db.py`** — `CongressDataBase`. Hand-rolled SQLite schema with typed constraints
(`CHECK(is_late IN (0,1))`, `CHECK(days_to_file >= 0)`) and parameterized inserts. Ingestion is
idempotent: `INSERT OR IGNORE` against a primary key means the pipeline can be re-run on a
schedule without duplicating rows.

**`informed_traders.py`** — two classes:

- `CongressData` wraps the upstream API (filers, all trades, per-filer trades) and loads into
  SQLite. Includes schema-drift handling that back-fills missing upstream fields at load time so
  a vendor-side column change doesn't break the pipeline.
- `CongressTradeAnalyzer` runs the analyses against the local database.

## Analyses

| Method | Question |
|---|---|
| `calculate_lag_analysis()` | Does filing delay correlate with returns? Buckets trades into lag quintiles and flags trades in the top quartile of both delay and return. |
| `analyze_sell_timing()` | Did they sell before drawdowns? Flags sales followed by a >5% 30-day decline. |
| `bet_size_vs_performance()` | Does a bigger bet mean a better outcome? Buckets estimated position size into quartiles and compares returns. |
| `portfolio_reconstruction(filer_id)` | Rebuilds a legislator's holdings over time by converting bracketed dollar disclosures into estimated share counts and replaying transactions chronologically. |
| `explore_transaction_types()` | Descriptive breakdown of transaction types, returns, and filing delays. |

### Two problems worth calling out

**Disclosures give dollar ranges, not share counts.** A filing says "$15,001–$50,000," not
"400 shares." Position sizes are estimated by taking a point estimate of the bracket and
dividing by the split-adjusted close nearest the transaction date. This is inherently
approximate, and the arithmetic midpoint is a biased estimator given the brackets are
roughly log-scaled — see limitations below.

**Transaction dates fall on non-trading days.** Prices are resolved by downloading a ±5 day
window and selecting the nearest date with a non-null close, so weekends and holidays resolve
to the adjacent session rather than returning nothing.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python informed_traders.py
```

`congress_trades.db` is committed, so the analyses run on clone without re-pulling the API.
To refresh from upstream:

```python
from congress_db import CongressDataBase
from informed_traders import CongressData

db = CongressDataBase()
src = CongressData()
src.update_filers(db)
src.update_trades(db)
```

## Known limitations

This is an exploratory pipeline, not a validated study. Being explicit about what it does
*not* establish:

- **Return columns are vendor-supplied, not computed here.** `ret_since`, `ret_30d`, `ret_1y`,
  and `excess_since` arrive pre-calculated from the upstream API and are populated on only
  1,087 of 5,000 rows (22%). The lag and bet-size analyses therefore run on a non-random
  subsample. Computing returns directly from price data is the top item on the roadmap.
- **No significance testing.** Current output is descriptive — quantile buckets and group
  means. No confidence intervals, no hypothesis tests, no multiple-comparisons correction.
- **No benchmark adjustment.** Raw returns are not decomposed into market beta and
  stock-specific performance, so a "good trade" may just be a rising market.
- **Short sample.** ~18 months and 75 active traders is low statistical power, and trades
  cluster heavily in a handful of tickers.
- **Selection bias.** Only disclosed trades, from members who file, in tickers the upstream
  parser resolves. Options and non-equity assets are excluded.
- **Midpoint estimator bias.** Bracket midpoints overestimate position size given log-scaled
  brackets; a geometric mean would be the better point estimate.
- **Portfolio reconstruction assumes positions start at zero.** Holdings acquired before the
  data window are invisible, and partial sales of such positions are currently clamped to zero
  rather than flagged as unexplained.
- **Tradeability.** A disclosure can be public 45+ days after the transaction. Any realistic
  strategy would trade the *disclosure*, which is a materially weaker signal than the
  transaction itself.

## Roadmap

- [ ] Compute returns and SPY-relative excess returns directly from price data (100% coverage)
- [ ] Add significance testing across lag cohorts
- [ ] Cross-reference committee assignments via
      [theunitedstates.io](https://theunitedstates.io/congress-legislators/committee-membership-current.json)
      — do members trade better in sectors their committee oversees? Within-person comparison
      gives a natural control.
- [ ] Fix portfolio reconstruction: track unexplained shares, key positions by `(ticker, owner)`,
      deterministic same-day ordering
- [ ] Switch bracket point estimate to geometric mean

## Background reading

- Ziobrowski, Cheng, Boyd & Ziobrowski (2004), *Abnormal Returns from the Common Stock
  Investments of the U.S. Senate*
- Eggers & Hainmueller (2013), *Capitol Losses: The Mediocre Performance of Congressional
  Stock Portfolios*

## Disclaimer

Educational and research use only. Built on public-record disclosure data. Not investment
advice, and not an accusation of wrongdoing against any individual — filing delays and strong
returns have many innocent explanations, and this pipeline cannot distinguish between them.
