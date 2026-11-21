# Five independent crypto PAPER experiments alongside NFI

The overnight fleet runs five source-derived native Freqtrade strategies independently. Existing NFI retains its strategy, configuration, database and simulated holdings. Atlas equity configuration and its installed checkout are unchanged. The Atlas window does not open: these are detached Docker workers with separate status and export controls.

This is experimental PAPER deployment, not a reproduction or certification of the published win rates. All five use Kraken USD spot, public market data, native simulated execution and a 0.4% per-side fee assumption. No private exchange credentials, Grok inference, real orders or external control API are enabled.

| Profile | Scope | Changes from pinned source |
| --- | --- | --- |
| E0V1E | Selected NFI USD universe, 5m | Official Freqtrade v3 conversion and native wrapper. Current defaults; published hyperopt settings are not bound to this source. |
| NASOS HOv3 | Same universe, 5m | Official v3 conversion. This is the pinned upstream strategy, not the separately repaired Freqle performance result. |
| BB_RPB_TSL | Same universe, 5m and 1h | Official v3 conversion, NumPy categorical compatibility, and an hourly ROC repair. Upstream mistakenly calculated the informative ROC from the primary dataframe and assigned it by integer index. Adaptation uses hourly candles. |
| CRSI | BTC/USD and ETH/USD, 4h | Long-only, SMA200 regime, CRSI below 30 entry, above 50 exit, 15-bar time exit. Removes SMA backward-fill. Native next-price execution differs from the source's close-price long/short test. |
| WaveRider | SOL/USD, BTC/USD 4h context | Public regime/volume/ATR entry logic with source SOL parameters. Freezes stop and first target in the persisted entry tag; exits the whole position at the first target. No private WFO, partial-target executor or AI component is reproduced. |

Original source bytes are downloaded from the exact commits and SHA256 values in `crypto_fleet.py`, verified, and retained under each profile's `upstream` directory. Converted copies and wrappers are separately hashed. The source shortlist and historical claim limitations are in `crypto-next-five-source-review.md`.

Each candidate has its own 10,000 simulated USD wallet and database, native available-balance allocation and six maximum simultaneous slots. The five wallets are not one shared 50,000 USD portfolio. There is no eight-hour expiry, daily entry-attempt quota, fixed share count or extra trial dollar cap. Native source conditions, stop/exit policies, exchange minimums, available funds, fresh completed candles and durable evidence still govern entries. Stronger trading activity is not guaranteed.

Preparation validates all seven source files and runs disconnected native tests in the pinned Freqtrade 2026.9 Docker image before producing launchers. The setup reuses the existing NFI-selected pair universe; it does not download a new historical dataset. It compiles and resolves every profile independently and checks original NFI configuration without starting workers. Original NFI data is never reset.

## Operator controls

Run `tools/prepare-crypto-weekend-fleet.ps1` with the exact reviewed revision. The printed `WEEKEND_DIRECTORY` contains:

- `Start-Crypto-Weekend.ps1`: launches the five candidates detached and starts or reuses the original NFI container. Does not open Atlas. Check status after warmup.
- `Show-Crypto-Weekend.ps1`: reads all six ledgers, runtime checkpoint freshness, container states, source signals, capture health and closed-trade results. A container being up does not prove fresh data or a healthy writer.
- `Pause-Crypto-New-Entries.ps1`: pauses entries across all six; running native exit management continues.
- `Resume-Crypto-New-Entries.ps1`: removes operator entry pauses. Restarting alone does not silently remove them.
- `Stop-Crypto-Weekend.ps1`: pauses entries and stops workers. Does not flatten positions. Restart the same workers to manage retained simulated holdings.
- `Export-Crypto-Weekend.ps1`: exports consistent SQLite snapshots, profile/config metadata, status and bounded log tails into a private review ZIP. Raw hash-chain journals remain on the PC.

Keep Docker Desktop running and the PC awake. PowerShell can close after detached launch. A new fleet preparation creates new candidate wallets; reuse the same launcher to continue the same test.

Native workers capture raw public OHLCV, analyzed candles and source signals, entry reviews, and changed simulated order responses. Journals have a 4 GiB / 8-million-record ceiling per worker run. Writer failures, incomplete/capacity-limited evidence or stale data block new entries; native exits continue. A completed recording is evidence, not a win-rate sample. Only closed lifecycles count toward wins/losses.

New containers share public-request pacing. A reused original NFI container retains its existing limiter; its running process is not altered. Market-data rate limits, pair availability and host resources can still cause forward failures; status and private logs expose these rather than claiming the disconnected checks prove an overnight run.

## Validation

On 2026-11-07 UTC, the cloud environment ran Freqtrade 2026.9 with CCXT 4.5.84 and all pinned source files. Thirty-one native/regression tests passed. Five real source classes loaded, and each passed a synthetic decision-to-native-persisted-unfilled-order contract. The synthetic fixture explicitly injects a BUY and mocks the exchange boundary: it verifies plumbing, not a source setup, fill quality or profitability. Tests also verify post-warmup indicator/signal prefix invariance, hourly ROC provenance, causal CRSI warmup, frozen WaveRider stop/target, stale-data rejection, dry-run credential/config sealing, original NFI regressions and real-write refusal.

PowerShell and Docker Desktop are unavailable on the cloud host. The installer repeats native tests and profile checks inside the user's exact pinned image, validates generated Compose, and refuses to produce a successful preparation if any check fails. No market worker or order path was started in cloud validation. No forward return or >60% performance is asserted for these adaptations.
