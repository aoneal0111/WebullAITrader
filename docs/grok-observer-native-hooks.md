# Passive native context hooks, disabled by default

Both native runtimes now accept an optional `RuntimeContextTap`. Desktop
composition can explicitly inject the same tap into Quick Scalper and the
Warrior sidecar/service. Default is `None`; no environment variable, launch
change, automatic writer construction or model activation is added. The current
installed workstation remains unchanged by the disposable test/export runner.

Composition rejects injection unless runtime and operational environments are
PAPER, live trading is disabled and Grok order review is disabled. This is not
`ATLAS_GROK_PAPER_ENTRIES_ENABLED`; that setting remains false. The external
owner must close the tap outside event/protection callbacks after stopping its
producers. Composition does not own an externally supplied tap or silently
restart it. Source/settings fingerprints still need to be bound to the chosen
baseline by that explicit trial owner before any real capture.

Warrior publishes references to its already completed market bars and, when its
existing execution-quote path runs, the existing quote and current signal state.
Scalper publishes bars from its existing point observations and its own
quote-reconfirmed policy assessment. Its stream path can consume the tap's
shared market-bar cache without borrowing Warrior's setup or assessment.
Scalper never starts waiting for bars in its trading logic: missing capture
history drops an observer record, not a trade. No additional provider request,
assessment, entry, exit or risk calculation is introduced by the tap.

Warrior's baseline result describes its native signal selection at the hook.
Scalper's describes quote-reconfirmed policy output before downstream account,
risk and admission. `ENTRY_CANDIDATE` is not execution authorization. Distinct
owner identities and assessment states remain in distinct context records.

Engine-thread work is a bounded tuple/reference queue submission; serialization,
bar validation and file work run on observer threads. Cache is limited to 128
symbols and 30 bars per symbol, event queue to 16 items, and the underlying
private capture retains its existing byte/record limits. Hook exceptions are
contained, and queue/storage/capacity/history/provenance failures remain counted.
No model/client callback or observer return value enters trading decisions.
This is bounded staging, not a hard real-time latency guarantee; actual native
capture throughput and event-path timing remain unmeasured on the workstation.

Native capture accepts the canonical Webull execution source/snapshot contract
and its shared provider quote timestamp basis. It preserves actual quote times;
unsupported source wrappers are counted and refused, never relabeled as Webull.
Supplied raw price fields are never fetched again or manufactured from snapshots.
Each cached bar must match its symbol and be completed and known by the quote
assessment. `available_at` is the actual observation frame's known-at time, not
a claim about original durable historical arrival. An old frame does not get
retimed to make it current. These are trusted local pipeline/type boundaries,
not cryptographic provider authenticity checks.

Default composition launches no tap, watcher or inference client. Readiness of
the isolated native hooks does not activate real recording or online inference.
Capture's `desktop_wiring=NOT_ATTESTED` field now reflects that the low-level
writer cannot attest where its owner attached it. The runner reports actual
desktop activation OFF; unit tests use temporary fixture inputs only.

The archive's version 3 summary separates crossed quotes, future timestamps and
combined failures. `quote_anomalies.crossed` and `.future` can overlap; `.both`
reports that overlap. Rejection reason counts remain mutually exclusive. Future
offset min/max are sanitized timing values only, not a diagnosis of clock error.
Original records and freshness rules are unchanged; no timestamps are repaired.

Run `-GrokCapture -GrokExport` on the pinned research source to test capture/hooks
offline and produce the small updated archive summary. No Atlas launch, online
data/model requests, strategy or flag changes occur. Keep private JSONL files
local and share only `summary.json`. Actual desktop capture, online credentials,
budget enforcement/usage, and online model latency still require explicit trial
setup; none is activated here. These tests do not establish profitability.
