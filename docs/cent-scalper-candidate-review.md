# Independent 5–10 cent scalper review — 2026-10-26

The requested behavior is repeated quick $0.05–$0.10 per-share profits across
regular-session opportunities, independent of Warrior's breakout decisions.
That is the objective of a new disconnected candidate, not an installed
profitable strategy or a permission to force trades throughout the day.

## Why the installed desktop can scan without taking positions

At c7b9f06, `RELEASE_PROMOTIONS` in `app/equity_overhaul/admission.py` is empty.
Desktop BUY submissions are wrapped by qualification regardless of controller
flags; Warrior also has an earlier qualification callback. Therefore neither
engine has a qualified production BUY route. Protective SELLs/cancels remain.

Quick Scalper's `observe_stream_event` already operates without a Warrior
setup or completed-bar gate. Its old policy uses adaptive targets/costs,
5-minute maximum hold, 120-second cooldown, and three attempts per symbol per
hour. Those mechanics differ from the new candidate's fixed cent objectives.
No flags, release approvals, risk limits, or running desktop source were changed.
The uploaded log establishes scanner activity, not the exact blocking reason
for every unfilled scalper opportunity; per-engine funnel evidence is still needed.

## Public references checked

- [Alpaca example-scalping](https://github.com/alpacahq/example-scalping):
  independent per-symbol state and immediate sell placement after a buy fill.
  Its example uses a 20-minute moving-average crossover, minute-bar events,
  and can leave the sell pending until end-of-day liquidation. The authors
  explicitly describe accumulated small profits being overwhelmed by losses.
  Reuse the state-management idea, not an assumption of profitability or its
  indefinite holding behavior. No Alpaca subscription or broker migration needed.
- [Freqtrade Scalp](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/berlinguyinca/Scalp.py):
  one-minute EMA/stochastic/ADX entries, 1% ROI, -4% stop. Its many-concurrent-
  trades recommendation and loss/target relationship do not match this account.
- [Freqtrade SmoothScalp](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/berlinguyinca/SmoothScalp.py):
  one-minute indicator example, 1% ROI and -50% stop in reviewed source.
  That is a poor fit for bounded small-profit stock scalping.

These are public implementation references, not audited winning Atlas results.
This patch independently expresses new quote rules; it copies no framework code,
adds no dependencies, and installs no other bot/execution owner. URLs reflect
source reviewed on this date; they are not an immutable external source lock.

## Frozen offline candidate

`app/equity_overhaul/cent_scalper.py` has no desktop/broker wiring. One symbol
per replay; compare two fixed cases rather than optimize parameters:

- Three rising BID and ASK quotes over 1–3 seconds, at least 1 cent BID advance;
  no HOD, completed candle, Warrior approval, RVOL or percentage-gainer filter.
- Quote age 0–2 seconds; explicit regular-session/tradable/not-halted evidence;
  enough displayed shares and round-trip estimated costs smaller than target.
- Arm on the third quote and hypothetically buy at the **next** usable ASK plus
  0.5 cent slippage. Cancel if it gaps over 2 seconds, reverses, becomes unsafe,
  or has already advanced a full target before entry. Never fill a signal price
  retroactively or open and exit on the same quote.
- Net target 5 cents or 10 cents per share, after 0.5 cent slippage per side
  and $0.0035/share per-side fees. Ten shares. Hypothetical exit crosses BID;
  no assumption that a resting limit touch guarantees a fill.
- 5 cent stop distance; sell at actual available BID minus slippage, including
  worse gap prices. Time exit after 60 seconds at the next valid quote;
  session-end exit when explicit non-regular evidence arrives. Invalid/stale
  evidence cannot fill protective exits. Never invent a terminal liquidation.
- Ten-second cooldown; new evidence can re-arm. Report residual inventory,
  open mark (only against usable quotes), pending entries, gaps and costs.

A 5-cent stop is a research setting, not evidence that every symbol provides a
valid structural stop there. Actual routing would additionally require valid
structure, tick rules, portfolio risk, durable stop/target acknowledgements,
partial fills, cancellation/restart reconciliation and explicit qualification.
Quote timestamps/provenance and complete session coverage must be verified
outside this simulator. Stops can slip and open exposure is not closed profit.

## Offline use

`tools/compare-cent-scalper.py --quotes <single-symbol.jsonl> --output <new.json>`
uses no credentials or network. Each JSONL row supplies symbol, received_at,
quoted_at (aware ISO dates), bid, ask, bid_size, ask_size (decimal strings),
regular_session, tradable and halted (booleans). Provider and receipt times must
both strictly increase; normalize/deduplicate feed evidence explicitly first.
Inputs are bounded at 16 MiB/100,000 rows. Reports stay private; they include
hypothetical trade detail, costs, input/source hashes and `promotion=NONE`.

No representative actual quote replay has been completed here. Synthetic
regressions verify accounting and causal mechanics only. The existing PAPER
baseline remains fixed, with no qualified new BUY strategies.

## Crypto diagnostic repair

The observed mixed success/417 responses cannot identify the cause. Official
[Webull crypto snapshot documentation](https://developer.webull.com/apis/docs/reference/crypto-snapshot/)
describes 417 business errors with a separate `error_code`, including an
`INVALID_PARAMETER` example. It is not sufficient proof of missing subscription.
The provider now retains at most 32 failures and logs an exact allowlisted code.
All other codes become `UNRECOGNIZED`. Bodies, messages, request IDs and arbitrary
code strings never enter output. The readiness CLI includes those redacted
failures in its small report. Retries, cadence, exception classification,
quote timestamps, freshness and protection behavior remain unchanged.
