# Modified ORB source review, October 13, 2026

Purpose: find reproducible modified opening-range methods, including advertised
80%+ win rates, for future Atlas comparisons. No external code is installed,
no external trading result reproduced, and no current entry/exit rule changed.

| Source | What was inspected | Decision for Atlas |
| --- | --- | --- |
| [ashok-jaga-gmail/opening-range-breakout](https://github.com/ashok-jaga-gmail/opening-range-breakout/blob/main/ORB.md) | Author's research log reports QQQ 0DTE option configurations with 90.6% wins across 53 trades, using a 75% premium stop and partial targets; a broader 289-trade long/short variant reports 79.2%, with a 30% premium stop. | Closest match to the user's SPY/QQQ direction-to-call/put goal. Investigate exact options fills, spread, costs, expiry/strike selection and out-of-sample outcomes before reuse. No reuse license was verified in this pass; no code copied. |
| [Same project's ORB/VWAP bot](https://github.com/ashok-jaga-gmail/opening-range-breakout/blob/main/ibkr/orb_vwap_filter/README.md) | Distinct VIX-gated, next-minute-open QQQ options configuration reports 80.0% over 100 trades in January–June 12, 2026, versus 74.5% over 263 trades in 2025. Uses +20% premium target, -75% stop, OTM-1, and declared $0.65/contract/side fees. VWAP entry filtering is off by default. | Concrete 80% claim worth reproducing; author identifies long-biased market exposure and different live/backtest size and trade caps. Not evidence for fixed $0.05/$0.10 scalps or an 80% forward win rate. |
| [Concretum Python tutorials](https://concretumgroup.com/python-matlab-backtesting-tutorials/) | Authors supply research-oriented ORB notebooks, with next-bar directional entry, H/L or ATR stops, and Alpaca history. They explicitly distinguish educational notebooks from their production stack and report material data-source sensitivity. | Preferred transparent baseline for a reproduction experiment. Their directional opening-window method must remain distinct from a confirmed range-break/retest method. No 80% Atlas result established. |
| [sam-bateman/trading-orb](https://github.com/sam-bateman/trading-orb) | Revisited project claims and the existing Atlas code audit in `external-strategy-comparison.md`. That earlier audit inspected versioned runner/backtester code and found assumptions needing repair. | Retain as a comparison/reference candidate, not a drop-in engine. The earlier four synthetic checks did not reproduce its historical returns. |
| [prashantsah567/ORB_Strategy](https://github.com/prashantsah567/ORB_Strategy) | MIT license and author-described two-year stock backtest, 1-minute entries and 5-minute exits, stock ranking, commission/borrow assumptions. Author warns sample files are insufficient and preprocessing fills missing data. | Candidate rules reference; inspect missing-bar handling and actual signal logic before adaptation. Published returns are author claims, not Atlas validation. |

These are four inspected primary sources, not the earlier requested twenty
completed reproductions per asset. Search results also surfaced small Python
ORB reference projects, but uninspected README snippets are not evidence.

## What the high win rates mean

The QQQ research log's 90.6% figure corresponds to 53 trades and a substantially
wider percentage stop than its tighter-stop variant. It reports testing hundreds
of parameter configurations. Those facts make untouched chronological validation
and exact option execution important. Its high rates stratified by eventual MAE
are retrospective descriptions: future maximum adverse excursion is unavailable
at entry and must never become a live filter. Percentage premium targets are
also different from the user's desired fixed $0.05/$0.10 premium moves.

The same repository's eight-year QQQ share benchmark reports much lower win
rates (42.8% for its R2 exit). Underlying shares and options results are distinct
experiments. Neither transfers automatically to SPY or micro futures.

## Candidate experiments

Freeze separate variants before evaluating: opening-window directional baseline,
confirmed 5/15/30-minute range breakout, breakout/retest entry, volatility-scaled
stops, and partial-plus-runner exits. Declare universe/session, timestamps,
underlying versus derivative fills, expiry/strike, position size and costs.
Use volume/spread as ranking/context consistent with the user's existing
preferences; do not silently import universal hard RVOL/spread thresholds.

Price-path gaps remain unresolved. Chronological train/validation/holdout
boundaries, code commits, the number of parameter trials and data coverage must
be retained with each experiment. The Grok learning reviewer can nominate
future trials from frozen paired comparisons, not certify an 80% win claim.
