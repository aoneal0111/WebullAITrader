# Daily research through the normal Atlas desktop

The operator opens the existing Atlas desktop shortcut and clicks START normally. Recording is an opt-in background component of `create_desktop_composition`, not a terminal-only bot and not a replacement window. The installer never launches the GUI. Screenshots can be taken from the normal dashboard, scanner, order and position views. Include the symbol and visible time when sharing a screenshot so it can be matched to the private recording.

## Existing components reused

| Existing component | Role |
| --- | --- |
| Native Webull market observer | Subscribed quote, trade, depth/status events already received by Atlas |
| Canonical paper runtime event sink | Orders, partial/full fills, marks and lifecycle events already emitted by the simulator |
| RuntimeContextTap | Separate Warrior/Scalper assessments, completed bar evidence and paired quote timing |
| ContextCapture | Typed causal context, provider timestamps, baseline identity and no execution authority |
| Freqtrade pinned lab and frozen Kraken history | Separate crypto historical strategy simulation, unchanged |
| Equity overhaul replay/admission | Separate strategy evaluation and qualified-only production gate, unchanged |
| Existing historical tranche reports | Research episodes/memberships; not automatically counts of executed trades |

## Added recording

`ATLAS_DAILY_RESEARCH_ENABLED=true` enables recording only with normal PAPER mode, live trading disabled, Grok order review disabled, and no second injected capture tap. It never enables a strategy or weakens a quote/risk check. The installer changes only this setting, backs up the private configuration, and preserves the existing strategy configuration. No automatic Grok requests are made. No credentials are requested.

Each normal app launch creates an explicit, unique local directory under `%LOCALAPPDATA%\AtlasResearch\daily-research-*`. These files remain private; nothing uploads them automatically. They contain market prices and simulated trading evidence and must not be committed publicly.

- `research-manifest.json`: application source file hashes, effective configuration fingerprint, start time, recording bounds and coverage limitations.
- `journal/events-*.jsonl`: ordered, hash-chained market events, paper lifecycle events, bar windows, stream assessment/rejection reasons and REST assessment inputs/results. Provider times and local receive/monotonic times remain distinct. Duplicate unchanged bar windows are counted rather than repeatedly stored.
- `journal/journal-status.json`: periodically refreshed counts, queue losses, storage failures, capacity status and recent evidence for diagnosis. No provider requests are made to refresh it.
- `private-capture/private-observer-context.jsonl`: bounded accepted fresh, causal Warrior/Scalper contexts. Raw stale/future assessments remain in the journal but are never relabeled as fresh model inputs.
- `research-summary.json`: shutdown receipt, journal state, original context-file SHA, tap timing and whether native policy settings changed. Recording notices changes without reverting or authorizing them.

Writer handoff uses bounded queues and `put_nowait`; hashing, serialization and disk writes run on dedicated workers. No model is attached to market, quote, order or protective-exit callbacks. Queues can overflow under load: drops are explicit and mark coverage incomplete rather than blocking the trading thread. Tests hold the writer and demonstrate continued nonblocking submissions.

The raw journal is limited to 512 MiB and one million records, segmented at 16 MiB. Fresh contexts are separately limited to 10,000 records/64 MiB. The recorder runs at most 16 hours and closes when Atlas closes. It does not silently delete prior sessions. High-volume sessions can reach these caps; inspect status before treating any session as a complete replay. This first implementation cannot promise an entire market day at every symbol/tick rate.

Normal Atlas entries, exits and chart display retain their existing permissions. An empty approved-policy registry still means no equity buys. Recording is readiness infrastructure, not a profitable strategy or automatic permission to paper trade unqualified candidates.

## Historical side-by-side model review

After closing Atlas normally, use `tools/compare-daily-research.ps1 -Session <exact directory>` to verify receipts and prepare a zero-request plan. With explicit `-Online`, it performs at most two paid historical reviews, one per engine, in independent parallel lanes. All native event references, full-context hashes, model evidence hashes and original quote/bar timestamps are retained. The native baseline result is withheld from model input to reduce copying it; the comparison report retains it beside the response.

This is a historical WATCH/PASS evidence review, **not** a PnL backtester or independent execution strategy. It does not feed any result to Atlas. It does not turn a recorded quote into a current executable quote. It refuses changed receipts and policy settings and refuses online use when journal coverage is incomplete.

The research-only transport sets `reasoning.effort=low` for the documented supported grok-4.5/4.6/4.7 models, reduces maximum visible output from 2048 to 256 tokens, uses a 15-second timeout and no retries, and sends one context per owner concurrently. These are request settings and client timeout controls, not proof of sub-two-second inference or a hard cap on billed reasoning. Other model identifiers keep provider reasoning defaults. The [official reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning), checked October 28, 2026, states grok-4.7 defaults to high effort and supports low; omitting this setting was a likely latency contributor in the earlier research client. The same separate profile is now used by newly prepared background observers. Existing prepared worktrees keep their old source until re-prepared. Actual response latency and whether it would have fit a two-second live budget are reported. A 20-second model response is never made acceptable to a two-second trade gate by extending timestamps or freshness. The native path never waits for these reviews; existing protective exits remain local.

Run the comparison without `-Online` first. Existing observer scripts expecting the old five-minute capture manifest should not be pointed at this daily archive.

## History readiness boundary

The installer writes a read-only `history-inventory.json` listing known research receipts, locations, sizes, modification times and bounded file hashes. It excludes clone/source trees and credential backups, does not open installed databases, and does not export performance values. Receipt presence does not validate bar coverage, corporate actions, venue fees or sample independence.

The new archive can support future adapters using the events actually subscribed to and recorded. It cannot invent pre-start ticks, data for unsubscribed stocks, options Greeks/chains, historical borrow availability, news arrival times or fills that never occurred. A new repository must declare which fields, warmup, universe and cost assumptions it needs; assess coverage before running it. Existing Kraken archives remain market-data evidence for Kraken, not Webull executable-price history.

## Validation and rollout

Local tests cover the normal desktop composition with recording enabled and runtime still stopped, fixed baseline configuration, separate engine taps, preserved stale-quote rejection, hash-chain tampering/truncation, queue pressure, storage caps, credential-field exclusion, policy-change detection, historical causal input identity, explicit paid-request boundaries, and private configuration backup. Synthetic tests demonstrate software behavior only. Windows checks, a normal visible desktop run, sustained data coverage, actual latency and venue-specific forward-paper results remain operator validation steps.

## Native observer lifecycle correction

The first normal-desktop recorder wrapped the native market observer in a function. Although market calls reached that function and raw events were saved, the function hid the original observer's dynamic protocol: `start`, `stop`, scanner/decision/profile binding, history preload, retained symbols, session policy and failure handling. The broker driver discovers these methods with `getattr`; losing them prevented normal native engine initialization. Earlier construction-only tests did not exercise this lifecycle boundary.

`RecordingMarketObserver` now delegates attribute lookup to the original observer and intercepts only market calls for the optional journal. Original callback results and exceptions are preserved; recorder failures cannot prevent native dispatch. Regression checks start and bind both a real CompositeMarketEventObserver/Warrior sidecar and the observer passed by the normal desktop composition, then deliver synthetic market events and require native bar evidence in the journal. They do not launch the GUI or connect a broker.

`engine-readiness.json` now reports lifecycle/tap binding and native evidence counts during recording. The final summary separately labels sessions with no engine evidence as `NO_ENGINE_EVIDENCE_REVIEW_REQUIRED`, even if their raw journal was loss-free, and retains numeric entry-funnel diagnostics. Raw journal integrity is not evidence that strategy evaluation ran. Existing recordings and settings are preserved; the installer enables recording only and does not change strategy eligibility or entry authorization.
