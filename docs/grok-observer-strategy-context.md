# Disconnected Grok strategy context

`check-weekend-observer.ps1 -GrokContext` validates completed-bar context and
demonstrates two synthetic records through the actual observer serializer with
a fake transport. It makes zero data or model requests, needs no API key, and
does not launch Atlas or change installed files or configuration. The local
PAPER/live/Grok-review mode check still applies.

The observer accepts an optional immutable `StrategyContext` paired with its
typed quote `Snapshot`. Owner, event, symbol, capture time, provider label and
baseline fingerprint must match exactly. Five to thirty immutable one-minute
OHLCV bars must be ordered, contiguous, complete and available by capture.
The final bar must be current within one minute and precede both quote sides.
Prices/volume are bounded decimals; setup state and the recorded baseline
assessment are restricted enums, not arbitrary instructions or order payloads.

Warrior's context focus is momentum structure, local pullbacks and extension.
Quick Scalper's focus is short-duration local support and nearby range. These
are observation prompts, not new trading rules or a replacement strategy. They
do not alter either engine or require Warrior to wait for a high-of-day breakout.
The short bar window cannot establish full-session highs, day percentage gain,
catalysts, scanner ranking or profitability. Missing evidence stays missing.

Each result carries a SHA-256 of its serialized strategy evidence. That is a
traceability fingerprint, not attestation that data or settings came from the
installed desktop. Provider labels and baseline assessments remain supplied
by the caller; no authentic workstation exporter has been wired in this change.

Existing request bounds and role cooldowns remain in force. Both quote sides
must still be fresh within two seconds before and after the response. Slow
advice is recorded as expired. Only WATCH/PASS observations are accepted; no
order, size, stop/target change, controller callback or tool action is added.
Outputs have no authority to change an entry, protective exit or PAPER baseline.

The existing quote-only prototype remains available. New context is injected
explicitly in Python; the older quote JSONL adapter does not accept it. No online
client construction, paid inference, model search tools, private performance
upload or desktop integration is enabled by this diagnostic.

Before a meaningful online observer trial: implement a provenance-checked,
read-only context exporter consumed outside GUI/protection threads; retain
private local audit logs and measured latency/token usage; validate xAI API
entitlement and explicit spend bounds using current official pricing. Keep
`ATLAS_GROK_PAPER_ENTRIES_ENABLED=false`: it enables an order-review path and
is never an observer-only switch. Live trading and Grok order submission stay
disabled. Offline fixture success does not establish online model behavior.
