# Atlas capability awareness

The Dashboard uses operator-facing names that describe Atlas behavior rather
than storage concepts:

- **Atlas Focus** is the actual active scanner universe supplied by the
  existing `WatchlistProjection`. It never inserts sample or placeholder
  symbols.
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
