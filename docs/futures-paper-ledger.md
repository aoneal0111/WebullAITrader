# Isolated futures paper ledger

`FuturesPaper` adds durable offline positions and quote-proxy exits to the
existing contract planner. It never calls a broker, market-data service or AI.
No desktop Futures tab or live trading path is activated. Options-data access
and Grok setup are independent and remain unchanged.

## Instrument and money handling

- Explicit FUTURES contracts with supplied expiry, positive multiplier, tick
  and margin. Whole lots, long or short. No equity-share or option-premium
  accounting is substituted for futures accounting.
- Default synthetic account cash 10000, per-trade risk 25, aggregate open risk
  50, maximum five lots per position and two open contracts. These are simulator
  settings, not broker margin requirements or recommendations.
- The caller declares per-contract fees. Default adverse slippage is one tick
  per side. A fresh entry quote must match the planned executable ask for longs
  or bid for shorts; otherwise the plan must be refreshed.
- Longs enter at ask plus slippage and exit at bid minus slippage. Shorts enter
  at bid minus slippage and exit at ask plus slippage. P&L uses direction,
  contract multiplier and whole-lot quantity, then subtracts both-side fees.
- Entry fees reduce cash. Supplied margin is reserved separately, not debited as
  purchase notional. New entries need cash for all margin reservations and the
  open positions' remaining exit fees. Closing releases reserved margin.
- `margin_headroom` uses realized cash only and excludes unrealized P&L, as
  explicitly reported in each snapshot. There is no mark-to-market account
  equity, daily variation settlement, maintenance-margin call or forced
  liquidation model.

## Deterministic lifecycle

Entries require local explicit activation; stop, target and max-hold protection
continues while entries are disabled. Declared hold is at most one hour and must
end before supplied expiry. Quotes must be current, ordered and tick aligned.
Missing/stale/rewound data retains the position and reports
AWAITING_FRESH_QUOTE. Expired contracts report EXPIRY_REQUIRES_RECONCILIATION;
there is no invented settlement, roll, exercise or final cash adjustment.

Planned risk includes entry/exit slippage and fees. Stop gaps can exceed that
reservation and are recorded as larger simulated losses. Negative-price futures
and nonpositive simulated fills are unsupported; they require a separate model
rather than clamping to an imaginary execution price.

One position owns each contract symbol. Identity deduplication survives reopen,
and an intent ID cannot bind to another plan. Cash-changing journal times cannot
move backward: a later realized profit cannot fund a backdated entry.

## Storage and checks

A dedicated database marker prevents opening an options or unrelated ledger as
futures. SQLite transactions atomically record cash, realized P&L, positions,
margin, intent IDs and journal rows. Transactions also serialize competing
connections and snapshot reads. Monetary settings must match on reopen; use a
separate ledger for another cost experiment. Restart preserves positions and
starts with entries disabled. Options and futures share Decimal-preserving plan
serialization, including the absent option strike on a futures contract.

```powershell
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_paper.py `
    tests/asset_modules/test_options_paper.py `
    tests/asset_modules/test_contract_logic.py -q

& .\.venv\Scripts\python.exe -m app.asset_modules.futures_paper_check
```

The check runs synthetic long/short round trips with invented contract
specifications and prices in a temporary database, then removes it. The P&L
verifies arithmetic only, not strategy performance or attainable fills.

Before desktop/broker execution: actual contract discovery and specification
verification, calendar/session/roll policy, timestamped contract quotes,
controller admission, engine workers, GUI projections and optional AI proposals.
Historical strategy comparisons also need appropriate futures data, transaction
costs and portfolio/margin modeling. The paper ledger is not evidence that the
initial breakout strategy is profitable.
