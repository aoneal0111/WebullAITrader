# Atlas capability awareness

The Dashboard uses operator-facing names that describe Atlas behavior rather
than storage concepts:

- **Atlas Focus** is the actual active scanner universe supplied by the
  existing `WatchlistProjection`. It never inserts sample or placeholder
  symbols. Its empty state explains whether Atlas is evaluating, waiting for
  the next scan cycle, or paused for a missing capability.
- **Atlas Activity** replaces the former static market-summary panel. It is a
  compact view of facts already present in runtime and application read
  models, including universe size, projected candidates, positions, pending
  orders, market-data connectivity, and broker connectivity.
  Unavailable facts display as `Unknown`; the view never estimates statistics.
- **Mission Status** summarizes the objective, runtime mode, market session,
  AI Scanner, decision engine, risk engine, and runtime health. The objective
  remains `Unknown` until an objective is supplied by a runtime read model.
- **AI Thinking** shows the latest projected decision reasoning when it exists.
  Without reasoning, it uses descriptive operational states such as
  `Searching`, `Waiting for next scan`, or `Managing active positions`; those
  labels do not claim or synthesize model reasoning.
- **Mission Timeline** is the existing immutable runtime/timeline projection
  with its filters and event details preserved under mission-control naming.
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
