# Descriptive recorded-outcome performance

Equity and simulator feedback now include win rate, mean net PnL (recorded net
expectancy per closed outcome), average winning/losing PnL, gross winning/losing
PnL, profit factor and largest-loss concentration. Existing engine, campaign,
ledger, strategy-family and policy-version grouping is preserved. Equity and
simulations are never pooled. Unknown historical versions/families stay UNKNOWN.

Win rate is strictly positive outcomes divided by all closed outcomes, including
breakevens. Profit factor is gross positive PnL divided by the absolute gross
negative PnL. With no losses the numeric value is null and state NO_LOSSES; an
empty sample is NO_CLOSED_OUTCOMES. There is no invented infinite ratio. Average
win/loss is null when that class is absent. Gross losses and average loss retain
their negative sign; largest_loss_share is a positive fraction of gross losses.
Means/ratios use 28 significant Decimal digits; totals preserve the native
high-precision crypto amounts within explicit input bounds.

These metrics use already-recorded net PnL and its existing cost/provenance
scope. No extra fees/slippage or quote execution quality are inferred. They are
descriptive, not independent holdout evidence, risk-normalized returns, portfolio
drawdown or proof of policy superiority. Position sizes and holding periods can
differ. A high win rate can still have negative expectancy: seventeen gains of
5 and three losses of 40 produce 85% wins but -35 net and -1.75 mean outcome.

New Grok learning reviews receive these fields in their existing separate
observational/proxy context. Cached reviews and immutable outcome payloads are
not rewritten. Reimport identities remain unchanged. Metrics do not alter
candidate eligibility, activate policies, train model weights or start an API
request. Existing transport/context limits remain in force.

Read the saved journal without importing or changing any records:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.learning_performance_check `
    --journal data/research/learning/learning.sqlite3
```

The reader uses a read-only consistent SQLite snapshot with bounded rows/bytes
and query deadline. Missing files are not created. Older comparison-only journals
show empty outcome groups without migration. Source collection remains a
separate step; this report describes saved outcomes, not current source coverage.
