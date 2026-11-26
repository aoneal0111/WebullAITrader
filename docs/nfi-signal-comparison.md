# Pinned NFI condition and universe comparison

The native diagnostic on October 27, 2026 completed indicators and signal
calculations for BTC/USD and ETH/USD without a long signal. That proved the
signal pipeline ran in that separate diagnostic process. It did not prove
why the previous process stayed flat or establish profitable trading rules.

## Source review

Reviewed NFI X8 v18.0.163, commit
`2884d15885a6996445861ce204068d1908761b5a`, file SHA-256
`3a39affe50eb4884867f8c4c481dcd8b4170a9a7eb2072f698a0ff1fce9b81c1`.

- There are 32 enabled long-entry conditions in the pinned defaults.
- BTC and ETH are both included in top-coin and grind eligibility lists. USD
  is supported as a stake currency. There is no blanket exclusion of these
  base assets. The header's reference to BTC/ETH pairs concerns quote/stake
  currencies; it does not say BTC/USD and ETH/USD are prohibited.
- Entry conditions combine base 5m and informative 15m, 1h, 4h, and 1d data.
  Completed calculations alone do not prove every required input is finite.
- Some conditions require pullbacks/oversold relationships and trend filters.
  A rising market alone need not trigger them.
- Kraken selects 710 startup candles in this pinned source.
- The upstream header suggests a much broader universe (40–80 pairs). Our
  two-pair, two-position, capped-stop/no-additions lab is an adaptation;
  author results cannot be assigned to it.

Sources: [pinned source](https://github.com/iterativv/NostalgiaForInfinity/blob/2884d15885a6996445861ce204068d1908761b5a/NostalgiaForInfinityX8.py),
[Kraken OHLC contract](https://docs.kraken.com/api-reference/market-data/get-ohlc-data).

## New bounded study

`tools/run-nfi-signal-study.ps1` runs an explicit public-data capture followed
by a disconnected signal comparison in the pinned Freqtrade Docker image.
It never starts a trading loop or a Grok manager. It mounts the existing lab
read-only and never changes its configuration, leases, database, or files.

The fixed comparison basket is BTC, ETH, SOL, LINK, LTC, AVAX, XRP, and ADA,
all against USD. This is a convenience basket, not a profitability ranking or
an account-specific entitlement check. Only online pairs in Kraken public
metadata are captured. Missing baseline BTC/ETH data fails the capture.

Capture uses at most 42 public reads: exchange time, pair metadata, and five
timeframes per pair. Each response is bounded to 2 MiB and 15 seconds. Public
requests have a fresh nonce to avoid reusing cached HTTP responses; there are
no API keys or authenticated endpoints. One frozen exchange-server cutoff
excludes uncommitted candles. Candle alignment, ordering, finite OHLCV values,
and price/volume consistency are validated. Failure manifests are preserved.

Candle files and the source manifest are hashed. Analysis checks those hashes,
blocks sockets, and runs inside `docker run --network none`. Its native
trade state is flat in an empty SQLite memory database. NFI's mutable cache
is redirected to a temporary directory, never the original lab. The frozen
provider supplies only captured frames and preserves native informative
merges. All original signals, thresholds, and configuration limits remain.

The comparison observes NFI's unchanged AND reducer and returns its original
result. For each enabled long condition it counts failed component masks
on eligible rows. The original helper is restored even on failure. Source
line mappings are usable only when `source_mapping_exact` is true. False
masks may reflect missing inputs; missing indicator columns are reported
separately. An OR-composite inside one component is not decomposed further.

## Interpretation limits

Kraken OHLC provides only up to 720 recent entries and includes an unfinished
last candle. After removing uncommitted data and 710 startup rows, the eligible
comparison window can be only a handful of candles. Do not call the initial
2.5-day input history a 2.5-day evaluated sample. Read the actual eligible
count and first/last evaluated timestamps for each pair.

Signal rows are not independent trades. The study does not simulate native
entry confirmation, order pricing/fills, fees, exits, slippage, portfolio
constraints over time, or profit. It establishes no win rate and promotes no
strategy or pair list. Current availability also cannot reconstruct a past
historical universe. A longer profitability comparison requires archived
history and a separately frozen execution-aware validation protocol.

The summary is `signal-comparison.json`. Unit tests use synthetic candles
and helper functions. Actual package/source execution and public captures
are reported separately from those tests. Windows execution in the pinned
container remains an operator check until its output is supplied.

## Public snapshot execution on October 28, 2026

The bounded capture completed all 42 public reads with a frozen server cutoff
of 11:28:40 UTC (06:28:40 America/Chicago). Actual execution used the pinned
upstream file and Freqtrade 2026.9 in a separate Python 3.13 environment;
its dependency set is not the pinned Docker image. The native source was
executed, not a handwritten approximation of its indicator rules.

| Pair | Eligible five-minute rows | Long signal rows |
|---|---:|---:|
| BTC/USD | 10 | 0 |
| ETH/USD | 10 | 0 |
| SOL/USD | 10 | 0 |
| LINK/USD | 10 | 0 |
| LTC/USD | 9 | 0 |
| AVAX/USD | 9 | 0 |
| XRP/USD | 9 | 0 |
| ADA/USD | 9 | 0 |

The evaluated range ended at the 11:20 UTC candle open and began at 10:35 or
10:40 UTC, depending on captured row count. Thus this result covers only
45–50 minutes of completed candle intervals after startup. It is not evidence
that the basket never trades or cannot profit over a longer period.

All 32 enabled long conditions were observed for every pair. Every component
ordinal matched its pinned source-line mapping. For example, conditions 1,
4, 21, 41, and 42 frequently passed their other top-level components but
failed their final setup component. Those include EMA separation, price
below moving averages, oversold relationships, and/or volatility filters.
Do not remove those clauses solely to produce trades.

`DC_HIGH_7D` and `DC_LOW_7D` were missing across eligible rows because their
base-timeframe calculation requires 2016 candles plus a shift. Their direct
long use is condition 8, which is disabled by default. This limitation does
not explain all absent signals in the enabled modes. AVAX also had one row
missing CVD inputs. Missing columns are reported explicitly, not treated as
zero or replaced with invented history.

No pair-list change was promoted. A broader basket alone did not create
signals in this short snapshot. No Grok inference, native trading loop,
account connection, or installed Atlas database was used.
