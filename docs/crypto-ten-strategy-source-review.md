# Ten public crypto strategy candidates: source verification

Reviewed 2026-10-31. This is a reproduction queue, not ten independently verified profitable Atlas strategies. No upstream code was installed or executed, and no trading settings changed.

## What was checked

Three GitHub repository search pages returned 300 metadata records, 297 unique repositories. Selected primary READMEs, strategy files and result artifacts were inspected. This is not 297 full code reviews. Exact upstream commits and available file blob hashes are in [the source lock](crypto-ten-strategy-source-lock.json); the metadata survey is in [the survey](crypto-repository-metadata-survey.json).

## Candidate queue

| Priority | Candidate | Published evidence | Verification and Atlas fit |
|---|---|---|---|
| FIRST_REPRODUCTION | [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity/blob/bbddbd94f97f3c14336387d2958183870891b951/README.md) — 5m spot multi-pair | Author spot summary: 289 trades; average win rate 96.95% (not a verified pooled rate). | Report is not bound to current X8 code/config. Source recommends 40–80 pairs and 6–12 positions; Atlas two-pair capped lab was a different experiment. |
| FIRST_REPRODUCTION | [BB_RPB_TSL](https://github.com/jilv220/BB_RPB_TSL/blob/33f83596e23ca214ef12352802aa798cb986b236/README.md) — 5m spot pullback | v104 report: 1,866 trades, 1,696 wins, 90.9%; worst trade -33.31%; printed drawdown 57.71%. | NFI derivative, not independent alpha. Recover v104 exact implementation/config before reproducing; current master/report binding unresolved. Old drawdown definition needs reconciliation. |
| FIRST_REPRODUCTION | [Connors RSI with BB slope](https://github.com/MRCVEngineer/CRSI/blob/d826a8e4d55c0d7a993e6f06303fcdb34205d31e/README.md) — long/short mean reversion; verify bar interval from dataset | Optimized slope CSV: 884 trades; 68.7% wins; Sharpe 1.56; max drawdown 20.4%. | Final validation uses long/short, close-price entries; explicit trading costs not found in inspected runner. Different baseline/dashboard variants have different results. Atlas long-only next-price fills require separate adaptation results. |
| SECOND_REPRODUCTION | [MomentumRegimeBasket15mFast](https://github.com/nateemma/strategies/blob/9bb7dab5df53203e69f60336749551a40767af0b/README.md) — 15m long-only spot basket | Strategy docstring reports +35.6%, +166.2%, +834.5% across three windows; no portable verified win rate. | Fast override is 14-day lookback. Author says cross-regime validation incomplete and documents liquidity/mark-to-market caveats; base strategy and universe required. |
| ADAPTATION_REQUIRED | [E14 Honest VWAP mean reversion](https://github.com/ojeology/mean-reversion-vwap-lab/blob/5ae2853d729b6761d4be3c2ccb7602beb32665cf/README.md) — 1m long/short; 20x source simulation | Committed honest result: 184 OOS trades over 15 test days; 43.48% wins; profit factor 1.7367. | Different from golden headline. Source uses 20x leverage; cannot transfer results to Webull spot. License not detected. Reproduce original separately, then 1x long-only with fees. |
| SLOW_CONTROL | [Apex No Pyramid](https://github.com/EstebanSP23/crypto_systematic_research/blob/5df0c43f6d48b7d8dbb74843d6747e5ddbb6819b/2_strategies/02_apex_no_pyramid/README.md) — 4h long-only 180-day breakout | Exam window: 47 trades, 38.3% wins, +0.20R expectancy; 6.1% APY; -14.1% drawdown. | Not an all-day scalper; sparse signals. Preserve next-open and partial-exit mechanics. Check notional cap as well as risk sizing. |
| SLOW_CONTROL | [Weekly 5EMA with daily 200EMA filter](https://github.com/EstebanSP23/crypto_systematic_research/blob/5df0c43f6d48b7d8dbb74843d6747e5ddbb6819b/2_strategies/03_five_ema_filter/README.md) — weekly long-only trend | Author V1: 31 trades, 41.9% wins, 38.6% APY, -40.2% drawdown. | 2018–May 2026 source period. No fixed native stop; any Atlas protective-stop addition changes the strategy. Not a frequent-entry option. |
| HOLD_FOR_VALIDATION | [Chart-pattern shared portfolio](https://github.com/juriok/chart-pattern-backtester/blob/2972c880144997ec305dc3dac23d78ee5a831176/README.md) — 4h long/short; 2x notional | README: 2,342 trades, 52.8% wins, PF 1.31; +296% return. | Author admits full-period pattern selection; profit heavily short-side. Results not committed as ledger. Source says 5m variant lost 88%; not a validated scalper. License not detected. |
| HOLD_FOR_ACCOUNTING | [BTC regime Bollinger configuration 97](https://github.com/Ksloveyuan/btc-trend-trading-backtest/blob/5fca4e3cc727ea273ab515dfb93f3d2152347316/README.md) — 4h / daily; long/short; 3x | CSV: 249 trades; 57.43% wins; final capital 27908.27 from stated 5000; total_pnl 12965.97. | Final capital minus starting capital does not equal reported total_pnl. Reconcile accounting before trusting headline 458% return. Raw data absent; license not detected. |
| HOLD_FOR_SOURCE_BINDING | [Multi-coin EMA 9/21 published comparison](https://github.com/Chamanrajragu/purffle-trader/blob/bfa29a696944cbf4062e6fe866d748c4a26b19b7/README.md) — reported multi-coin trend; shipped bot M1 | README 2-year comparison: +35.4%, 27.8% wins, 53.1% drawdown. | Shipped M1 bot uses EMA crossover OR RSI oversold, whereas README says AND. No committed backtest engine/ledger identified for comparison. Published winner not established as shipped implementation. |

## First implementation sequence

1. Reproduce NFI in its own pinned Freqtrade environment with the author's full spot configuration and universe. The old two-pair capped lab was an Atlas adaptation, not a full reproduction.
2. Recover BB_RPB v104 source/config binding and reproduce its trade ledger, including large losers and drawdown. Do not rank by its win rate alone. NFI and BB_RPB share ancestry.
3. Reproduce the exact CRSI slope variant, then add explicit venue fees, spread/slippage and causal next-price fills. Keep source long/short and Atlas long-only results separate.
4. Compare the three with the same frozen dates, symbol eligibility, capital, fees and fill assumptions. Preserve an untouched later evaluation period and record every parameter choice. Then paper forward-test the survivors with independent budgets and full capture.

A source-faithful baseline and a constrained Atlas adaptation each need their own identity and report. An adaptation's failure does not establish that the upstream strategy failed. Conversely, a README's positive result does not demonstrate Atlas performance.

## Comparison record required

Each run needs source commit/file hashes, engine/dependency versions, exact config, exchange/feed, symbol universe and delistings, timezone/bar convention, data hashes and missing-bar coverage, warmup, signal/decision/order/fill timestamps, accepted and rejected signals with reasons, modeled fees/spread/slippage, shared wallet and position limits, gross/net PnL, open mark-to-market equity, drawdown, average winner/loser, profit factor, expectancy, trade count, win rate and evaluation dates. Reconcile ending cash and equity to the complete fill ledger. Run future-data leakage checks before interpreting a backtest. Model review latency belongs in its own log; native execution must not wait for research commentary.

## Claims that did not survive source inspection

- CacheCarti/Crypto-Strategies at f8e32599c192b823dbe7aea77a2b9341c6114527 advertises ten seven-stage survivors. Its ten passed cards instead report two stages and negative out-of-sample returns; its index contains only 406 rejected entries. Preserve this discrepancy rather than calling the ten winners.
- AKzar1el/walk-forward-crypto at 8ec32ea1ad454edd88abc5118a808c6fa764063b explicitly withholds the selector implementation and parameters in docs/REDACTION_REPORT.md. Its published winning-week percentage cannot be treated as trade win rate.
- Purffle's shipped signal is EMA crossover OR RSI oversold; README describes AND. Its profitable comparison is not bound to that shipped bot.

## Boundaries

No subscription, exchange connection, bot installation, Grok request, live order or PAPER activation is part of this review. GPL obligations and repositories with no detected license require resolution before code redistribution. Missing licenses are metadata findings, not a legal determination. Public author statistics above have not been reproduced here and are not audited live performance. No private Atlas performance or credentials are included.
