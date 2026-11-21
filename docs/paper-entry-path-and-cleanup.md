# PAPER entry path and stopped-worker cleanup

The NFI forward engine can process valid fresh candles without emitting an entry.
Source `enter_long=0` is a strategy decision before the Atlas entry wrapper. Do
not remove quote/candle checks or inject BUYs to turn that into trading activity.

## Disconnected entry contract

`tests/strategy_lab/test_nfi_native_entry_path.py` exercises Freqtrade 2026.9
`create_trade`, its native signal reader, stake/price validation, `execute_entry`,
Atlas capture gates, and persistence to an isolated SQLite database. The inputs
are explicitly synthetic. The exchange boundary returns a pending test order
with zero filled quantity. It does not contact Kraken, run Atlas, use a user's
wallet, or demonstrate an NFI source setup or a profitable fill.

Run in an environment containing the pinned native runtime:

```sh
PYTHONPATH=. python -m pytest --confcutdir=tests/strategy_lab -q tests/strategy_lab/test_nfi_native_entry_path.py
```

The positive case must persist one tagged unfilled order. No-signal, operator
pause, stale data and capture-not-ready cases must persist none. The real CCXT
write methods remain blocked. This contract deliberately leaves NFI source
rules and its sealed config unchanged. Source reproduction performance and
forward trade frequency require separate evaluation.

## Cleanup correction

The sidecar previously ignored a false return from report-worker close, cleared
its worker/writer references and marked itself stopped while the report could
still be persisting. It now raises a specific observational-report pending
error, retains ownership and reports degraded health. Execution has already
been revoked; Start refuses another session until Stop finishes draining the
existing worker. A subsequent Stop can complete normally.

Desktop teardown catches only this specific report error, finishes independent
resource and capture cleanup, and returns false to report the incomplete stop.
Other execution-worker timeouts still propagate before the durable execution
store closes. This fixes ownership and false clean-stop reporting; it does not
claim to make an arbitrarily slow report finish within five seconds.

Outcome collection now records SQLite's numeric error code and symbolic name
when available, in its existing last-cycle snapshot and warning. It excludes
SQL text, paths and raw exception messages. A historical OperationalError
without those fields cannot be retroactively diagnosed as locking or corruption.

The installer runs offline regression checks before a fast-forward update. It
starts no process and preserves trials, expiry, budgets, strategy settings and
wallets. An expired trial still requires an explicitly prepared new test.
