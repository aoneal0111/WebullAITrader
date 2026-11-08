# Crypto parameter tests and BB exit repair

The existing NFI, E0, NASOS, BB, CRSI, Wave and ROC wallets are retained. No experiment enables live trading, calls Grok or starts Atlas.

## Confirmed execution defect

Freqtrade's version-3 converter updates BB's `hasattr(trade, 'enter_tag')` check but leaves `trade.buy_tag` and a local `buy_tag.split()` reference. The resulting callback exceptions can be suppressed by the native strategy wrapper. The adapter now consistently uses `enter_tag` and retains the plural `buy_tags` list. Original pinned upstream bytes and trading parameters are unchanged. This repairs intended exits; it does not create new entry signals.

`tools/repair-crypto-bb-tags.ps1 -Revision <reviewed-40-character-commit>` stages and checks the repair before touching the old BB worker. It pauses entries, gracefully stops BB only, backs up code/profile/compose, installs the tested adapter and reuses the same wallet/configuration. Other services remain unchanged. A previously running BB is recreated; a previously stopped BB stays stopped. Entries resume only after a fresh healthy recorder checkpoint and fresh ready pairs, and only if the operator had not paused them. A failed health check leaves entries paused. It never restores an old trading database over new activity.

The script repeats native checks in the pinned Docker image on Windows. Cloud validation uses Freqtrade 2026.9 on Python 3.13; Docker Desktop/PowerShell execution still occurs on the operator's PC.

## Entry structure and changes tested

| Family | Existing entry structure | Experimental change |
| --- | --- | --- |
| E0 | RSI/EWO or CTI pullback; original short-average discount can be 5.8% | Reduce discount to 2%; loosen fast RSI/CTI |
| NASOS | EMA discount, RSI and EWO branches | 1–2% EMA discount, RSI ceiling 65, EWO thresholds 2/-12 |
| BB | Combined dip/momentum branches with informative hourly indicators | Selected EMA discount 1.5%, dip RSI 32, fast RSI 50 |

Each family retains an original control and an altered-entry control. The 24 additional variants combine 2%/4% initial hard stops, 0.5%/1% net exit floors, 1.5%/2.5% profit targets and a 24-hour time exit. Source custom stops and negative custom exits remain active. These are research overlays, not author-reported performance.

The grid was written before this rerun. It follows earlier exploratory 2024 tests and is not an untouched discovery sample. Development is January–February 2024; validation is March–April 2024. Six Binance USDT pairs are used: BTC, ETH, SOL, XRP, DOGE and LINK. Initial simulated balance is 10,000 USDT per strategy with six slots, fee 0.4% per side. The later 2025 holdout and 0.8% fee stress are opened only for a frozen candidate passing both earlier periods. No explicit slippage model is claimed.

Native hyperparameter objects are copied per strategy to prevent one subclass changing another's defaults. The tests assert actual native parameter values, exercise BB's real custom exit, and invalidate any replay containing suppressed strategy callback errors. Forced period-end exits are reported separately from closed-trade win rate; their losses remain in terminal PnL. A high closed win rate cannot hide losing open inventory.

Pinned five-minute data and metadata are verified against the existing source locks. Hourly candles are aggregated only from complete five-minute groups. Recorded December 2025 exchange metadata does not establish 2024 rules. Binance backtest returns do not establish current Kraken USD profitability.

No failed variant is installed into an existing paper worker. The result tables and selection refusals accompany the completed research archive.

## Completed result

All 60 native runs (30 configurations across two periods) completed with zero strategy callback errors. The 28 regressions passed. No faster variant passed both periods, so no parameter variant is promoted; the 2025 holdout and higher-fee stress remain unopened. The BB code repair is separate from this performance decision.

Each table entry uses closed trades excluding forced period-end liquidation. Net PnL includes the terminal liquidation and modeled fees, from an independent 10,000 USDT start per run.

| Variant | Jan–Feb closed / win rate | Jan–Feb net USDT | Mar–Apr closed / win rate | Mar–Apr net USDT |
| --- | --- | --- | --- | --- |
| Original E0 | 5 / 80.0% | +229.92 | 12 / 91.7% | +277.01 |
| E0 altered entries | 52 / 86.5% | -20.33 | 128 / 82.8% | -361.20 |
| Original NASOS | 9 / 100.0% | +267.82 | 11 / 90.9% | -188.24 |
| NASOS altered entries | 24 / 100.0% | +513.01 | 58 / 91.4% | -1,148.01 |
| Original BB, repaired tags | 6 / 83.3% | +139.53 | 21 / 71.4% | +159.09 |
| BB altered entries | 15 / 86.7% | +94.42 | 46 / 82.6% | -215.61 |

The original E0 and repaired BB controls were profitable in these two research periods but generated few trades. Faster entries increased activity while worsening terminal results. A higher win rate alone would select losing settings. Twenty-four hard-stop/target variants were also tested; their full results and rejection reasons are in `crypto-parameter-results-2026-11-08.json`.
