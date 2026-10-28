# Cent scalper portfolio study on recorded quotes

This is a disconnected research adaptation of the existing `cent_scalper.py` prototype. Warrior, the installed Quick Scalper and the equity qualification registry are unchanged. Nothing in this study can start Atlas, request market data, call Grok or place broker orders. All cases remain unqualified regardless of their results.

## Frozen experiment

The CLI writes `frozen-protocol.json` before reading quote events into the candidate. Four cases are reported without selecting a winner: net target 5 cents or 10 cents per share, each at base half-cent or stressed one-cent slippage per side. A 0.0035 dollar per-share fee on each side is an explicit research assumption inherited from the earlier prototype, not a verified Webull account cost. The fee is subtracted once on each fill; bid/ask prices already include the spread and it is not charged again as a separate fee.

The shared virtual wallet starts at 1,000 dollars. Each case has at most two positions, up to ten whole shares and 500 dollars per position, at most 10% participation in both displayed sides at entry, a five-cent price stop, 60-second holding limit, ten-second symbol cooldown, six entries per symbol per hour and a 20-dollar realized session loss limit. Existing holdings keep their protective exit path when new entries are blocked. Results stop admitting entries after 5,000 completed trade records; this is an explicit research bound, not a performance-tuned rule.

Signals require three distinct rising BID **and** ASK observations spanning 1–3 seconds. The spread must be positive and at most one cent, price at least one dollar, and spread plus modeled fees/slippage below the net target. There is no Warrior score, HOD breakout or percent-gain requirement. The quote source must still supply admissible market-state evidence. A signal fills only at a later provider quote's ASK plus slippage; it is cancelled if stale, gapped, reversed, moved the full target already, or crossed a session boundary. Sizing occurs at fill time against the common cash and position limits.

Exits cross the next fresh available BID minus slippage and per-share fees. Target exits require the **net** target after both sides' fees, stop fills can gap beyond the intended stop, and thin displayed exit size results in partial fills. Once a partial exit starts, it continues on subsequent fresh usable quotes. A stale or halted quote cannot be used to invent a liquidation. Open and partial positions remain in the report. The ending liquidation mark is a hypothetical whole-account value only when all remaining positions have sufficiently fresh, sized observable bids. Marked drawdown omits unobserved excursions and is not a guarantee on losses.

Premarket, core and after-hours sessions use the existing Atlas exchange calendar. Changing sessions cancels pending entries and unwinds existing positions against the next usable quote. Unknown/closed sessions block entries. A detected wall/monotonic clock discontinuity disables further study entries while leaving fresh-quote exit processing available.

## Inputs and causal state

The adapter verifies the closed journal's hash chain, policy-unchanged receipt and exact native eligibility source hashes. It processes journal arrival order; it does not sort on future provider timestamps. Duplicate or regressed provider book updates do not create new momentum confirmations. Future timestamps are rejected without poisoning the watermark for later valid books. Invalid, crossed, missing-size and stale books are counted, not retimed or filled.

Older recordings lack a continuous tradability/halts heartbeat. They can provide a short two-second eligibility observation only when their recorded `QUICK_SCALP_STREAM` snapshot was emitted: the verified native snapshot builder returns no snapshot for an untradable or halted stock. This is **not** an independent universe or unbiased sampling of every subscribed symbol. The report explicitly counts books without current state evidence; they cannot open positions. A zero-trade result may therefore mean missing coverage as well as no candidate signal. The recorded native result is included as reference diagnostics, not treated as a comparable candidate PnL backtest.

A new passive `MARKET_ELIGIBILITY` heartbeat addresses this recording gap in future normal desktop sessions. The existing sidecar records observed `tradable`/`halted` state before any setup or entry filters. Unchanged state is coalesced at one second per symbol, changes are preserved, queue handoff is nonblocking, and no source timestamps or policy settings are changed. Explicit halt/state events revoke pending eligibility; a known halt remains binding until resume evidence. These observation bits do not attest account permissions, borrow, the complete venue book or broker execution. Their absence blocks new study entries.

The new heartbeat is not installed by running the study script, which uses an isolated reviewed checkout. The current recording can be studied immediately using its existing native proofs. Installing collection improvements into the normal desktop is a separate update; it does not install this candidate as an entry strategy.

## Output and validation boundary

`cent-portfolio-summary.json` contains every frozen case, coverage counts, signal/fill/rejection counts, closed-trade win rate/net, observed marked drawdown, cash and open inventory. Full simulated trades remain in per-case private files with hashes and at most ten examples in the small summary. No private performance values are committed to GitHub or uploaded automatically.

This short, retrospective, subscribed-symbol sample cannot qualify a strategy or establish year-long profits. Fills assume displayed quotes and do not model broker latency, historical depth consumption, queue priority, market impact, account-specific fees or all Atlas production gates. The new candidate must receive separately frozen history validation and forward paper execution evidence before activation. Do not weaken stale/future quote, halt, account, ownership, loss or qualification controls to make a replay produce trades.
