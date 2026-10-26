# Seven Grok roles and the separate online observer

## Authority and implementation

This follows the Atlas plan for Warrior, Quick Scalper, Crypto, Options and
Futures advisers, a Master coordinating proposals against account evidence,
and Learning reviewing paired experiments. One shared client/model supplies
the roles. Existing engine worker modules remain separate native owners;
the GUI is retained. This change does not install seven GUI bots, launch Atlas,
enable live trading, or create an autonomous policy/code-writing agent.

`GrokDecisionCouncil` adds one bounded, immutable cycle across all five engine
advisers, Master and the existing LearningJournal. It accepts at most 32 globally
unique native candidates with validity of at most 60 seconds. It copies evidence
before the first request and clears absent engines' old advice each cycle.
Each engine can select its own native IDs; Master can select among surviving
engine recommendations. Returned objects remain bound to the original typed
Candidate, intent, owner, revision and deadline. A failed review, asynchronous
stop, stale result or duplicate cycle cannot yield a dispatch grant. The cycle
limit is 100 and concurrent submissions are refused, never queued.

Supported proposal intents are ENTRY, CANCEL_ENTRY, REPLACE_ENTRY, REDUCE, CLOSE,
and TIGHTEN_STOP. A council decision is a command *plan*, not permission to
reserve funds, change a position, or place an order. Its execution_authorized
is always false. The existing controlled PAPER dispatcher independently handles
trusted, originally prepared equity entry placements. This council does not
replace that dispatcher or turn model-created JSON into a placement.

| Role/path | Available | Remaining activation boundary |
|---|---|---|
| Warrior / Scalper | Independent advisers, entry review/controlled PAPER handoff, fresh-context observer | Qualified native strategy, current risk/quote authorization and explicit PAPER review activation |
| Crypto | Adviser, native worker, separate paper ledger/allocation/protection components | Verified desktop activation and current native commands; proposals remain off |
| Options / Futures | Advisers, offline/native workers and paper allocation components | Desktop data/execution adapters are not connected |
| Master | Ranks original surviving engine candidates with copied account evidence | Allocation and order admission remain controlled native responsibilities |
| Learning | Durable paired-experiment review and nominations | No automatic policy promotion, strategy rewrite or model training |
| Non-entry management | Owned/revision-bound candidate protocol and council planning | Generic desktop cancel/replace/reduce/close/stop dispatch is not implemented here |

Learning can be explicitly requested for one pending paired experiment. It
cannot replace Master-selected commands. A slow learning request revokes a
command plan that expires while waiting. For active decision cycles, schedule
learning separately: five engine reviews plus Master can consume six requests,
so adding a seventh call in the same minute can exceed a six-RPM API key cap.
No review is retried automatically to work around a provider limit.

The equity release registry remains empty. This change does not qualify a
strategy, enable new BUYs, relax risk or freshness checks, or intercept native
protective stops. Actual management/market adapters must be implemented and
verified before describing every role as an automatically executing bot.

## Separate online observation trial

`app.asset_modules.grok_online_observer` is a separate command-line process. It
reads the existing private native context capture and uses GrokObserverTrial
for Warrior and Scalper. It has no controller, broker, ledger, order-service,
management-dispatch or settings-writing connection. The Atlas capture process
continues to block every GrokClient.request locally; that does not block this
explicit, separately launched observer process.

Explicit `--online` consent is mandatory. Bounds are 20 attempted inferences,
100 distinct source records, 1 MB input, 900 seconds waiting for a first fresh
record, then 300 seconds of observation. These request bounds are not a dollar
budget. Existing per-role 60-second cooldowns and the client's total attempt
limit still apply. Provider failures count; no automatic retries occur. Advice
arriving after its original two-second evidence expires remains ADVICE_EXPIRED,
not a usable decision. Missing, stale, malformed, fixture-only, revised or
wrong-baseline inputs cannot be turned into fresh market evidence.

The parent native capture writes observer-manifest.json with the baseline and
source/settings hashes. The supplied manual launcher verifies that manifest,
its associated capture path, the original and staged source receipt, and the
current effective configuration before constructing the online client. It
rechecks source/configuration at completion. It never retimes an old recording.
The direct --baseline API is also available for explicit controlled experiments;
its summary does not claim native manifest verification.

The client now reports provider-reported input/output token totals. Missing
usage and failed attempts without reported usage remain visible and must not
be interpreted as zero charges. Token counts are not a complete invoice: taxes,
provider pricing, caching, other usage details or tools are not priced here.
No web/X search tools are enabled. Real account credentials remain local.

The local private-observations.jsonl file includes symbols/model reasons and
should remain private. Share only online-results/summary.json. The summary has
status counts, per-engine coverage, attempt/token accounting, baseline checks
and zero observer order authority. It does not report profitability, approve
policies or claim readiness of other market adapters.

## Manual workflow

1. Keep PAPER, LIVE_TRADING_ENABLED=false and
   ATLAS_GROK_PAPER_ENTRIES_ENABLED=false. Crypto AI proposals remain unchecked.
2. With Atlas closed, run tools/prepare-grok-observer-trial.ps1 with an exact
   reviewed Revision. This creates a persistent pinned observer worktree and
   fresh capture of the installed baseline outside OneDrive. No merge or
   settings change occurs and no model/GUI is started by preparation.
3. In one terminal manually run capture/Start-Observer-Capture.ps1, then use
   normal START in Atlas. This is the user's explicit GUI launch.
4. In another terminal manually run Start-Grok-Observer.ps1. It starts only the
   separate observer, allows the bounded paid requests and cannot submit orders.
5. Share online-results/summary.json and capture/session-summary.json. Leave
   private records, source receipts, .env and keys local. New runs require new
   output directories. Changed source/configuration requires new preparation.

No authenticated provider call is made during development/tests. Offline fake
transports verify all five engine roles, Master and Learning nominations,
management ownership contracts, caller-mutation isolation, duplicate/stop/busy
handling, stale/future/native-source validation and usage accounting. Those
checks establish software mechanics, not profitable performance or installed
workstation activation.
