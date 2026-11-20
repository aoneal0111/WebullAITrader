# NAUT 0.40R/50% observed signal — bounded PAPER gateway replay

The October 9, 2026 capture reconciled three lifecycles. The losing NAUT
lifecycle showed a 0.40R/50% sampled drawdown signal at
`2026-10-09T16:54:15.199777+00:00` with BID `$2.420`.
It ultimately lost `$42.240` with a previous sampled mark of `+$19.200`.

The signal did **not** submit a real historical exit order. The new
`test_naut_observed_signal_gateway_replay.py` is a deterministic, synthetic
PAPER gateway exercise using the observed price and approximate 384-share
geometry. It does not claim the recorded market had the replay's bid sizes
or future liquidity.

- Test entry: 384 shares at $2.40, initial stop $2.29.
- Hypothetical order: 96-share **market** `PROFIT_HARVEST_1` using the
  existing `AutonomousPaperExecutionBridge.ensure_exit` route.
- The observed $2.42 BID is a **signal input**, never a credited fill.
- Subsequent injected test quotes supply 30- or 96-share liquidity at $2.41,
  followed by a $2.40 test quote when needed.
- A canonical STOP should remain active while the harvest is pending and
  after its partial or full completion.
- No runtime activation threshold or harvest fraction is changed.

## What remains unproven

1. Whether the actual NAUT order book at the signal timestamp could have
   filled 96 shares at a comparable price.
2. Whether a 0.40R/50% trigger should cause a full exit, partial sale,
   stop-tightening action, or some combination.
3. Durable restart recovery and cancellation failures at this exact signal
   still need separate end-to-end coverage.
4. Improved net expectancy across many trades, after costs and missed
   continuation profits, has not been established.

Do not promote this signal from offline research into production PAPER
without additional historical breadth, runtime integration tests and
controlled forward sessions. This experiment must not start Atlas or
contact a broker.
