# Atlas capability awareness

The Dashboard uses operator-facing names that describe Atlas behavior rather
than storage concepts:

- **Atlas Focus** is the actual active scanner universe supplied by the
  existing `WatchlistProjection`. It never inserts sample or placeholder
  symbols. Its scanning, idle, and capability-pause empty states are distinct;
  a pause includes the projected reason and automatic-resume expectation.
- **Atlas Activity** replaces the former static market-summary panel. It is a
  grouped metric-card view of facts already present in runtime and application
  read models: Universe, Evaluating, Candidates, Open Positions, Pending
  Orders, Market Data, and Broker.
  Unavailable facts display as `Unknown`; the view never estimates statistics.
- **Mission Status** is a vertically spaced status card for Objective,
  Runtime, Market Session, AI Scanner, Decision Engine, Risk Engine, and
  System Health. Values wrap instead of clipping. Objective wording is derived
  only from projected runtime and open-position state; otherwise it is
  `Unknown`.
- **AI Thinking** is the primary context panel. It displays Current Objective,
  Operational State, projected Reasoning, Last Decision, Confidence, and Next
  Evaluation. Reasoning and confidence appear only when a decision projection
  supplies them. Next Evaluation remains `Unknown` because the current runtime
  read models do not publish that fact. Operational descriptions are state
  labels, not synthesized reasoning.
- **Mission Timeline** is the existing immutable runtime/timeline projection
  with filters, event details, and ordering preserved. Trade, risk, scanner,
  broker, market-data, and system categories receive presentation-only color
  cues for faster triage.
- **AI Scanner** reports scanner readiness or a capability-aware pause.
- **System Health** separates infrastructure failures from unavailable broker,
  subscription, configuration, and market-session capabilities.
- **Portfolio Overview** summarizes projected account and position facts.

## Broker-independent capability model

`app.capabilities` defines immutable asset and session capability entries. The
asset vocabulary is Stocks, Options, Crypto, Futures, and Forex. The session
vocabulary is Regular, Premarket, After Hours, and Overnight. Every entry is
classified as one of:

- Available
- Unavailable (Subscription Required)
- Unavailable (Broker Not Supported)
- Unavailable (Configuration Required)
- Unavailable (Market Closed)
- Unknown

The model contains no provider-specific response codes. A broker adapter maps
its advertised support and detected runtime facts into this common snapshot.
The Webull adapter currently performs that translation; future adapters can
implement the same boundary without changing GUI or health read models.

## Refresh and overnight behavior

Capability snapshots travel through the existing runtime health event and
read-model pipeline. Session transitions, configuration refreshes, and market
data reconnects rerun the existing capability detector and publish a new
snapshot. System Health, the status bar, AI Scanner status, and Atlas Focus's
empty state therefore refresh from the next application-state notification.

An unavailable overnight market-data entitlement remains a scanner pause. It
is presented as `Unavailable (Subscription Required)`, not as authentication,
broker connection, or infrastructure failure. When a reconnect detects that
the subscription is available, the health projection updates and the existing
scanner startup path resumes automatically.

Atlas Focus's empty state distinguishes a capability pause from an ordinary
idle scan cycle and does not manufacture symbols.

## Runtime-only dashboard activity

The dashboard no longer carries a benchmark-symbol market summary. Atlas
Activity receives the same immutable application-state snapshots as the other
presentation surfaces and does not subscribe to quotes or introduce runtime
work. No default ETFs, benchmark tickers, sample symbols, or placeholder market
symbols are supplied by Atlas Activity or Atlas Focus.

## Mission Control presentation boundary

Mission Status, Atlas Activity, AI Thinking, and Mission Timeline are GUI
projections over the existing immutable application state. They do not add
logging, runtime instrumentation, market subscriptions, scanner work, or
execution behavior. Missing metrics and reasoning are rendered as `Unknown`
rather than inferred or fabricated.

## Commercial operator workflow

1. Start with Mission Status to confirm the runtime, market session, scanner,
   decision engine, risk engine, and overall health are in an expected state.
2. Use AI Thinking as the primary operational narrative. Treat Reasoning,
   Last Decision, and Confidence as projected facts; `Unknown` means the
   runtime has not supplied the value.
3. Check Atlas Activity for available scale and connectivity facts, then use
   Atlas Focus to inspect projected opportunities. Empty Focus states explain
   whether Atlas is scanning, idle, or capability-paused.
4. When no symbol is active, the Market panel remains informational. It does
   not select a placeholder security or fabricate a price series.
5. Use Mission Timeline for chronological investigation. Category colors help
   locate important events, while existing filters and event ordering remain
   authoritative.
6. Use Positions, Orders, Decisions, Lifecycle, and System Health for deeper
   review after the overview identifies an item requiring attention.
