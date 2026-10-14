# Equity loss execution review

The supplied active-campaign export selected the three largest reconciled closed
losses from Warrior and Quick Scalper. This deliberately selected loss sample
does not measure strategy win rate or establish the effect of a policy change.

| Engine | Symbol | Recorded net PnL | Declared entry risk | Loss / declared risk |
| --- | --- | ---: | ---: | ---: |
| Warrior | DBGI | -595.13 | 99.880375 | 5.96 |
| Warrior | FEAM | -304.08 | 41.63 | 7.30 |
| Warrior | FSLY | -229.52 | 42.50 | 5.40 |
| Scalper | MANE | -178.30 | 30.60 | 5.83 |
| Scalper | WFF | -41.85 | 41.85 | 1.00 |
| Scalper | FLYE | -40.82 | 39.82 | 1.03 |

These ratios use BUY request risk metadata, not independently reconciled initial
fill risk. DBGI has no matching captured path; its protective SELL was created
the next morning. FEAM and FSLY entered October 9 and closed through STOP fills
on October 13. Their captures have large time gaps. The export cannot determine
whether Atlas was stopped, the feed was unavailable, or another fault caused
the gap. It cannot prove an executable earlier exit or recover an earlier price.

MANE entered October 8 at 19:59:58 UTC. Its stop sold 10 shares at 125.18 and
24 at 119.89 shortly after the regular-session boundary, versus a 125.90 stop.
That is fill-price evidence of severe overshoot, not evidence that a different
strategy or an AI adviser could guarantee a fill at the stop. WFF exited at its
stop; FLYE overshot slightly. All six historical policies remain unversioned.

## Reproduced current implementation defects and corrections

- Quick Scalper's runtime constructed the default profit-retention policy,
  ignoring its configured `maximum_holding_seconds`. Pass that duration into
  the policy; enforce expiration on the first fresh owned-position quote at or
  after it. Do not use stale quotes or change the five-second freshness guard.
- `SCALP_MAX_HOLD` and `SCALP_MOMENTUM_STALL` were submitted as fixed-price LIMIT
  profit targets. They now use the existing PAPER market-liquidation path used
  by session-close intents. Ordinary profit targets retain their limit orders.
- A working immediate MARKET exit has no limit price, which previously caused
  repeated management requests to cancel and replace it. Preserve it when its
  remaining quantity matches authoritative inventory, including after partial
  fills and a falling bid. Quantity/type mismatch still requires reconciliation.
- Full liquidation cancels residual BUY quantity of the same lifecycle first.
  Otherwise a partially filled entry can buy again during an exit. If entry
  cancellation fails, the existing bracket is retained and liquidation reports
  cancellation failure rather than dismantling protection with a BUY still live.

Ownership, position quantity, PAPER authority, cancellation failure handling,
and the already-triggered stop guard still apply. A successful order submission
does not guarantee a fill, price, profit, or bounded loss. Stops and exits are
local simulations: closing Atlas or losing its feed prevents continued matching.
This patch does not create a continuously running broker protection service.

The new regressions reproduce the defects before the patch and verify configured
45/300/600-second boundaries, market exit identity, falling prices and partial
fills. They are mechanics tests, not a trading-performance backtest. The supplied
losses did not contain a maximum-hold or momentum-stall exit, so these corrections
must not be reported as proven causes or retroactive fixes for those losses.

The requested greater-than-60-percent win rate remains a validation target.
Entry-selection changes require representative, versioned outcomes and held-out
replays with costs and executable quote coverage; net profit and loss economics
must also improve. Do not infer a winning configuration from this loss sample.
