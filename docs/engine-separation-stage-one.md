# Atlas engine separation: first stage

Atlas retains its account workspace and existing asset tabs. Two additional tabs,
Warrior and Scalper, show orders with exact lifecycle ownership. Selecting these
tabs does not start an engine or change execution policy. Sidebar routes reuse
the same strategy ledger rather than allocating another copy of its tables.

The net-filled inventory is an order-snapshot ledger, not a reconciled position
projection. Orders without ownership remain visible in the account workspace;
they are not guessed into either strategy. A cancelled order can have fills and
those fills count. Two strategies trading the same symbol retain separate
lifecycle rows. Account PNL is not attributed to a strategy from symbol alone.

## Readiness

| Engine | Current implementation | Required next integration |
| --- | --- | --- |
| Warrior | Shared equity runtime and dedicated ledger view | Isolate worker after protecting lifecycle and command ownership |
| Scalper | Shared equity runtime and dedicated ledger view | Same migration with independent policy/history |
| Crypto | Separate spot paper supervisor | Broker adapter and validated quote history |
| Options | Planning workspace | Actual contracts, bid/ask, expiry, multiplier, fees and execution adapter |
| Futures | Planning workspace | Contract specifications, margin, rollover, quotes and execution adapter |

Engine identities in `app.asset_modules.engine_catalog` are not asset classes,
and catalog readiness is not permission to submit orders. A master controller
must arbitrate account capital, exposure and idempotent commands before separate
workers can submit independently. Protection and matching must continue when an
AI request times out. Code proposals must pass isolated checks and paper trials;
active positions retain their policy version until closure.

## Offline premium target comparison

`app.trade_intelligence.knowledge.quote_exit_comparison` compares two default
price increments, 0.05 and 0.10, on one long instrument. For calls and puts,
provide the actual option's quotes, not its underlying's stock price. The input
contains `signal_at`, `stop`, and `quotes`. Each quote has aware `observed_at`,
`source_at`, `bid`, and `ask` fields in chronological order. One file represents
one instrument; the caller must verify its identity and contract terms.

Entry uses the first fresh observed ask strictly after the predeclared signal,
within five seconds. Exits use later fresh bids. Risk sizing includes declared
round-trip fees and a capital cap. Stops can exceed planned loss on a gap.
The default limits are two-second quote age, five-second observation spacing,
60-second hold, 10,000 quotes and a 2 MB CLI input. A stale/future quote or gap
ends the unresolved policy path; missing observations do not become wins.
Only policies closed on the same input qualify as a paired comparison.

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.quote_exit_comparison `
    verified_option_quotes.json --multiplier 100 --fee-per-unit-per-side 0.65
```

The fee above is an illustrative input, not a broker fee quote. Results are
quote-fill proxies with no queue depth, available size, latency, partial fills,
shared capital or overlap model. Tests verify mechanics, not profitability.
This module does not connect to a broker or promote a trading policy.

Research candidates include [pysystemtrade](https://github.com/pst-group/pysystemtrade),
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader),
[LEAN](https://github.com/QuantConnect/Lean),
[Freqtrade](https://github.com/freqtrade/freqtrade), and
[options portfolio backtester](https://github.com/lambdaclass/options_portfolio_backtester).
Their results are not reproduced by these tests. Audit pinned code, licenses,
data, transaction costs and out-of-sample evidence before adapting a rule.
Existing historical Atlas evidence remains available for knowledge lookup.
