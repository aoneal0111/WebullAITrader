# Isolated SPY/QQQ option paper ledger

`OptionsPaper` is an offline quote simulator following the existing native
contract plans. It is not a Webull adapter, GUI activation, AI strategy, or
profitability test. No broker or model is called. Futures, Crypto and the equity
runtime are unchanged.

## Supported mechanics

- Standard SPY/QQQ calls and puts bought long; 100 multiplier, whole contracts,
  maximum five per trade. Calls and puts share one position slot per underlying.
- Original premium targets of 0.05 or 0.10, a valid tick-aligned structural stop,
  and at most a sixty-second plan hold. Plans and quotes must be current.
- Isolated starting cash 2500, risk per trade 25, combined open risk 50 by default.
  Fees are supplied per contract per side. Default slippage is 0.01 premium per
  side. Entries pay ask plus slippage and exit at bid minus slippage.
- Ledger risk and reward are recomputed after entry slippage and both-side fees.
  A plan that no longer meets costs, risk or available cash is refused. Cash
  retains a reserve for open positions' exit fees. Existing open reservations
  reduce the aggregate risk budget.
- Stop, target and max-hold exits run with entries disabled and require a fresh
  quote for the actual contract. A stop gap can exceed reserved risk and is
  recorded as a larger simulated loss. Quotes with rewound source timestamps
  are rejected.
- Missing or stale quotes retain positions and return AWAITING_FRESH_QUOTE.
  Expired contracts return EXPIRY_REQUIRES_RECONCILIATION with no invented expiry
  fill, exercise, assignment or settlement.

There is no session calendar, real options-chain discovery, liquidity/depth or
partial-fill model. A quote snapshot does not prove an executable fill. Costs
are declared assumptions; supplied fees are not a claim about Webull's fees.
Entry activation is explicit and local to this isolated simulator.

## Durability

A dedicated ledger marker rejects unrelated existing databases before modification.
SQLite transactions atomically record cash, realized P&L, position ownership,
intent deduplication and the fill journal. An intent ID cannot be reused for a
changed plan or reopen a completed trade. Reopening the ledger retains positions
and starts with entries disabled. Changed monetary settings are rejected on
reopen; use a separate experiment ledger for another cost model.

BEGIN IMMEDIATE serializes competing ledger instances, preventing two contracts
for the same underlying from filling simultaneously. The journal is durable;
snapshot output returns only the latest 100 events.

## Offline verification

```powershell
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_options_paper.py `
    tests/asset_modules/test_contract_logic.py -q

& .\.venv\Scripts\python.exe -m app.asset_modules.options_paper_check
```

The check uses a synthetic contract/price path in a temporary database and
removes it afterward. Its artificial profit demonstrates multiplier, slippage,
fee and cash arithmetic only; it is not historical or live strategy evidence.

Remaining work before desktop trading: session/expiry calendar, actual option
chain and timestamped bid/ask feed, controller integration, engine worker,
position/order projections, and Grok proposal binding. Options remains unavailable
for broker execution and is not automatically activated in Atlas.
