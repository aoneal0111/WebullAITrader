# Crypto market discovery research

This package is a disabled-by-default, research-only sidecar. It discovers an
explicitly configured set of crypto pairs, or the bounded Webull crypto
instrument list when no set is configured, polls snapshots, computes bounded
continuous-market features, ranks explainable momentum events, and appends
decisions to an external JSONL file.

The flow is one way:

`Webull market-data client -> crypto provider -> bounded research worker -> JSONL + read-only GUI view`

It has no broker, order, position, risk, scanner-qualification, Warrior,
Adaptive Entry, PAPER, or LIVE interface. Every persisted decision carries
`asset_type=CRYPTO`, `research_only=true`, `production_promoted=false`,
`selection_authorized=false`, and `execution_authorized=false`.

## Webull capability boundary

The installed official Webull SDK exposes crypto instrument-list, snapshot,
historical-bar, and generic market-data streaming APIs. This milestone uses
only the instrument-list, snapshot, and optional historical-bar methods. It
does not subscribe to streams, mutate equity subscriptions, call trade APIs,
or claim broad screener support. The available universe is the provider's
bounded instrument-list response rather than a crypto screener.

Atlas does not currently have a crypto-aware catalyst/news abstraction.
Stock-oriented earnings and SEC enrichment is inapplicable; symbol-only news
providers are ambiguous without explicit pair and asset metadata. Crypto news
and catalysts therefore remain unsupported in this milestone.

The upstream API exposes crypto order endpoints, but they are intentionally
outside this package. Atlas paper and live crypto execution are unsupported and
unauthorized. Sandbox endpoint availability is not treated as proof that a
particular account can trade crypto.

## Identity and time semantics

Provider spellings are normalized into immutable `BASE/QUOTE` identities that
also retain the provider symbol. Compact configured tickers are rejected
because they cannot be split safely without provider metadata. Identity keys
include `asset_type`, so an equity-like `BTC` ticker cannot collide with
`BTC/USD`.

Crypto has the authoritative session `CONTINUOUS_24_7`. ASIA, EUROPE, US, and
WEEKEND are analytic regimes only; they never represent exchange authority or
change the existing equity session model.

## Bounds and failure isolation

Polling is lazy, timeout-bounded by its dedicated data client, rate-limited,
and explicitly retried. Queue admission is nonblocking. The queue, configured
symbols, retained symbol state, observations per symbol, and dedup signatures
are bounded. Provider, malformed-data, stale-data, queue-pressure,
persistence, and shutdown failures are contained and counted. JSONL work stays
off the producer path.

Configuration defaults:

- `CRYPTO_DISCOVERY_ENABLED=false`
- `CRYPTO_DISCOVERY_SYMBOLS=`
- `CRYPTO_DISCOVERY_REFRESH_SECONDS=60`
- `CRYPTO_DISCOVERY_QUEUE_CAPACITY=256`
- `CRYPTO_DISCOVERY_PATH=crypto-research.jsonl`
