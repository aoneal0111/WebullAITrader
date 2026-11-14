# Frozen Q3 2026 adapted-NFI historical screen

This stage follows successful native signal diagnostics. Short snapshots did
not establish profitable settings. The test now freezes an entire quarter
before reading its performance results.

## Data and fixed scenarios

Official source, verified October 28, 2026:
https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data

The page lists the full history through June 30, 2026 and the Q3 2026 update.
Full history is split across five binary parts. The importer treats them as
one seekable ZIP, then combines selected members with the Q3 update. The
source URLs are fixed to assets.kraken.com; no account or subscription is used.

Evaluation is July 1, 2026 inclusive through October 1, 2026 exclusive (UTC).
Warm-up inputs begin June 1, 2024, providing at least 710 prior daily candles.
The fixed basket is BTC, ETH, SOL, LINK, LTC, AVAX, XRP and ADA, against USD.
Required timeframes are 5m, 15m, 1h, 4h and 1d; 1m detail is included during
Q3 for the native candle-fill simulator.

Every scenario is reported; no result-dependent selection or optimization:

| Scenario | Pairs | Fee assumption per side | Limits |
|---|---|---:|---|
| BASELINE | BTC/USD, ETH/USD | 0.4% | $1,000 virtual wallet; $50 stake; two positions |
| BASKET | All eight fixed pairs | 0.4% | Same |
| BASKET_COST_STRESS | All eight fixed pairs | 0.6% | Same |

The fee settings are explicit research assumptions, not validation of the
operator's exchange tier. The stress case is additional transaction cost,
not a measured historical spread or queue/slippage model.

## Bounded archive import

`archive_history.py` uses HTTP HEAD and exact byte ranges to read only selected
ZIP members. It does not bulk-download or extract the multi-gigabyte archive.
It requires stable ETags, exact HTTP 206 ranges and lengths, bounded member
sizes, supported ZIP compression and unambiguous filenames. It reads selected
members to EOF for ZIP CRC checks and hashes each selected output file.

Limits: 700 public reads, 512 MiB response-byte budget, 30 seconds per request,
192 MiB uncompressed/compressed per selected member, four 4 MiB cache blocks.
The budget includes headers as requests and maximum metadata allocations.
The importer never extracts archive paths. Full-archive published SHA256 is
not verified by selective retrieval; integrity is HTTPS/ETag consistency,
member CRC, and frozen selected-file SHA256. These checks are not equivalent
to downloading and validating the whole published archive.

Rows are validated for timestamp alignment/order, finite OHLCV, price bounds,
nonnegative volume/trades, matching overlaps and boundary/startup coverage.
Sparse no-trade intervals remain absent in imported files; gap counts are
reported. The native Freqtrade loader may fill these intervals with flat
zero-volume candles. No real trades or synthetic volume are invented by the
importer. A partial manifest is retained on failure; existing outputs are
never overwritten by a new import. An explicit `--resume` (or PowerShell
`-ResumeHistory`) verifies existing candle hashes, the frozen period and
archive ETags/sizes, retains the prior manifest, and reuses completed frames.
The request/byte budget remains cumulative across resumes. PowerShell
`-ResumeHistory` can also reuse a complete dataset after an interrupted
Docker replay; that skips download and verifies all inputs before replay.

Current public Assets and AssetPairs metadata are also frozen. CCXT normalizes
that captured metadata offline; its market limits are present-day
approximations, not historical exchange/account entitlements.

## Native replay isolation

`run-nfi-history-replay.ps1` verifies the existing lab, creates an isolated
research worktree/output directory, runs tests, and imports the history.
It then invokes the pinned Freqtrade image using `docker run --network none`.
The original lab, source and history mounts are read-only. The process never
runs `freqtrade trade`, a Grok manager, or the Atlas desktop. Installed Atlas
settings and the original lab's entry leases/database are unchanged.

`AtlasNFIHistoricalReplay` refuses all modes except native BACKTEST. Its only
policy adaptation is a one-second simulated-time entry lease in its new local
replay directory. It calls the original AtlasNFILab confirmation afterward;
it does not override the parent's rejection. All native NFI indicators,
signals, custom exits, $50 stake cap, -2% stop, disabled position additions,
and protective-exit permission remain. Backtesting itself still uses its
native simulation assumptions. Entry attempts and parent accept/reject counts
are reported so a zero-trade result is diagnosable.

The engine sees frozen normalized public markets, an isolated backtest trade
state, and no original trading database. Mutable NFI caches and results are
inside each separate scenario directory. No exchange credentials or xAI
client are needed. Python sockets are blocked as a second guard beyond the
container network setting.

## Interpretation and release status

This is retrospective screening of a strategy selected after the tested
quarter. It is not a genuine prospective holdout and author tuning history
may contaminate the period. Pair-selection and survivorship bias remain.
Native candle fills cannot establish real executable prices, book liquidity,
queue position or the operator's actual costs. Current market precision and
minimums approximate historical values.

Report all trade counts, wins/losses/draws, net simulated PnL, profit factor,
drawdown, exit reasons, resolved limits and entry-confirmation counts. A
higher win rate alone does not qualify an adaptation. Lookahead and recursive
stability analysis are explicitly NOT_RUN at this stage; no strategy or pair
list is promoted, and there is no activation path in the historical tool.
The existing lab stays paused independently of these results. This is a
continuous-admission native-strategy screen: it does not replay bootstrap
duration/lease expiry, Grok decisions or the controller's -$20 campaign-loss
stop. It is not an end-to-end reproduction of that bounded controller.

Source references:
- https://www.freqtrade.io/en/stable/backtesting/
- https://www.freqtrade.io/en/stable/lookahead-analysis/
- Pinned NFI source and Freqtrade image in `app/strategy_lab/source-lock.json`.

Unit tests use synthetic archives/candles and fake parent callbacks. They
are not historical strategy performance. Native package/real archive checks
and the operator's Windows pinned-container results must be reported
separately. `historical-replay-summary.json` is the small upload artifact;
raw historical candles and detailed trade records stay local.

## Implementation validation

The focused suite passes 76 disconnected tests. Native Freqtrade 2026.9
initialization and historical replay were also exercised in a Python 3.13
package environment with all network connections blocked. That environment
is not the pinned Docker image; Windows/pinned-container replication remains
an operator check until its result is supplied.

The official archives were read selectively to assemble all 48 pair/timeframe
files. A real transport interruption after six pairs exercised the explicit
resume path: completed file hashes and source archive identifiers matched,
so completed frames were reused. The initial capture and resume together
used 274 public reads and approximately 262 MiB of byte budget. Archive
coverage, member CRC checks, output hashes, and complete quarter/startup
inputs passed. No raw candles or operator trade records are committed here.

The complete three-scenario native screen ran with broker orders, Grok calls
and replay-network calls all zero. The native entry and protective stop path
was exercised by an actual historical signal in the broader basket. Aggregate
performance is retained in the private replay summary, not published here.
No source rule, original lab file, Atlas setting or release registry changed.
