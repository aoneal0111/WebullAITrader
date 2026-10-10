# External momentum reference: audit and bounded comparison

Reviewed 2026-10-10. No external historical returns were reproduced and no
Atlas runtime trading rules were changed.

## ORB repository: inspect the engine before importing results

The current `sam-bateman/trading-orb` main commit is still
`23f2400a696296cddb00166dd429c0d048bee1bb`, the version inspected in
[external-strategy-comparison.md](external-strategy-comparison.md).
Re-read pinned `src/backtester_v2.py` blob
`f988d98ec57b04de5ae6b90a9939742fc9f731ef` and
`src/run_max_backtest.py` blob `a894227da9f2fa0e01980c8db1be1e18145bc2b0`.
All four previously recorded synthetic probes were rerun successfully:

| Synthetic case | Observed result | Adoption decision |
| --- | --- | --- |
| Flat-price 100-share round trip | Net -$5 instead of -$3 with one slippage charge and commissions | Do not import cost accounting |
| Long stop 99, next bar opens 95 | Exit 98.99 | Do not import stop gap fills |
| Target at 0.75 opening-range widths; break-even at 1 width | Target exit occurs before break-even activation | Do not assume the advertised break-even rule protects gains |
| AAA still open; last global bar is BBB | AAA exits at BBB's 499.99 instead of its own 100 | Do not import end-of-sample liquidation |

These reproduce engine defects, not their contribution to the reported
ten-year results. Keep the opening-range hypothesis as a baseline, but do not
rank Atlas against those headline metrics until the engine and data are repaired.
This supersedes treating this repository as the strongest implementation to copy.

## Concretum rule extraction

Source: [the authors' public Python tutorial](https://concretumgroup.com/backtesting-7-years-of-free-data-beat-the-market-an-effective-intraday-momentum-strategy-for-the-sp500-etf-spy/),
unversioned webpage reviewed 2026-10-10, Step 3 momentum signal/exposure code.

The inspected version samples the volatility-band/VWAP signal every 30 minutes
and delays exposure by one row. The upper band uses the larger of session open
and dividend-adjusted previous close, scaled by prior-session opening-move
history; the lower band uses the smaller. A long requires close above both the
upper band and VWAP. Otherwise the scheduled signal can flatten or reverse.
This is not a monotonic peak-profit stop or a continuously monitored exit.

## What the new reference implements

`app.trade_intelligence.knowledge.external_band_reference` implements the
momentum signal and exposure schedule only, with band multiplier 1 and default
frequency 30. It uses independently supplied completed-minute features.
It does not calculate or verify those features, reproduce the original sizing,
gap-reversal strategy, commissions, market-close liquidation, or place orders.
Both signed exposures are included for reference; this does not enable shorting
in Atlas. Custom API frequency is a distinct experiment, not published evidence.

The function reports each completed interval's prior exposure separately from
the new decision, which can affect only subsequent intervals. It cannot credit
a trade with the same interval's move that generated its signal. Missing
scheduled features, missing minutes, or mixed symbols/sessions cannot fabricate
an exit. Candidate PNL is always null: a signal is not a fill.

Inputs: JSON object with `rows`, at most 390 rows and 2 MB through the CLI.
One symbol/session, starting with the first completed minute:

| Field | Meaning |
| --- | --- |
| symbol | One instrument |
| session_open_at | Aware timestamp of actual session open |
| completed_at | Aware minute close timestamp, contiguous from session open + 1 minute |
| close, vwap | Completed bar close and cumulative session VWAP |
| session_open_price | Actual session opening price |
| previous_close_adjusted | Prior session close adjusted for the current dividend |
| sigma_open | Prior-session opening-move history for this minute, fractional units |
| history_cutoff | Latest historical observation used; must precede session open |
| features_available_at | Availability of current features; cannot follow completed_at |

The caller must verify session calendars, adjustment rules, volume coverage,
historical lookback, and feature derivation. A timestamp assertion cannot prove
absence of lookahead. Capture-export ZIPs are **not** this input format. Their
quote records lack the full prior-session minute history for sigma; filling in
that history with constants would invalidate a real comparison.

Run against an independently verified feature export:

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.external_band_reference features.json
```

Next compare original scheduled signals against Warrior's actual entries and
management on the same complete dataset. Keep failed follow-through and small
profit retention as separate policies; changing this reference to fit the
observed NAUT loss would no longer test the established external rule.

Validation: source schedule/lag parity, VWAP-based exits, neutral equality,
falling bands, missing coverage, future features, symbol isolation and invalid
values. Synthetic tests establish mechanics, not profitability. No new market
data subscription or credential is needed for these checks.
