# Crypto and Grok observation readiness — October 24, 2026

Reviewed research branch source at `55a36125e889e83fcbd640d1baf02b1c4a4233df`.
This review adds a disconnected observer prototype and tests only; it does not
modify the desktop composition, strategy baseline, risk limits or protective
exits. No Atlas launch, provider inference, credentials entry, subscription
purchase or private trading-performance publication was performed.

## Crypto readiness

Present in source (including the earlier `5d8ea1a` repair):

- Provider timestamps are required; receipt time cannot manufacture quote age.
- SDK exceptions are classified for 401/403 permission, 429 rate limit, 417
  business errors and retryable transport/server failures, without provider
  bodies or credentials in diagnostics. A 429 defers requests for 60 seconds.
- Snapshot batches are bounded to twenty symbols, matching the documented API.
- Historical acquisition completes before snapshot requests, preserving causal
  bar availability at the quote cutoff.
- Deterministic crypto entry proposals require a matching USD pair, finite
  uncrossed bid/ask and a nonfuture quote aged at most two seconds. Completed
  one-minute history must be current and contiguous.
- The isolated crypto PAPER ledger separately rejects missing bid/ask and
  nonfuture quotes older than ninety seconds. Its wider allowance is not the
  deterministic entry signal's two-second policy.
- Crypto proposal activation defaults off independently of research collection.

Readiness gaps found in the original `55a3612` inspection (follow-up status below):

1. `CryptoResearchRuntime.admit` checks old age but does not reject negative age
   (future timestamps) at research admission. Downstream deterministic entry and
   ledger checks do reject future timestamps. Treat this as a research data
   quality gap, not evidence that an invalid quote can authorize an order.
2. `refresh_once` increments complete refresh status on successful batch
   requests without proving every requested symbol was returned or accepted.
   `ACTIVE` can accompany stale/rejected rows; it is not a fresh-quote readiness
   certificate. Compare requested, returned, admitted, stale and malformed
   counts rather than relying on the status label.
3. Default snapshot cadence is sixty seconds. This does not supply continuously
   executable two-second quotes. A decision-time quote or validated streaming
   source must be checked before any enabled entry trial; do not relax freshness
   to accommodate the polling interval.

The workstation is not connected to this review. Current installed revision,
live timestamps, subscription permissions, symbol coverage and active local
settings were not read. Earlier successful tests establish code behavior, not
current provider availability. Keep crypto proposals disabled pending that
verification. These review findings do not change the frozen PAPER baseline.

Follow-up: research admission now rejects future quotes, and complete refreshes
require every requested pair to have a usable, fresh bid/ask and successful
admission (or a known duplicate). Missing symbols, stale/future quotes, queue
rejection and unexpected pairs no longer yield complete status. New counters
separate future quotes, missing pairs and research-fresh pairs. Research freshness
still uses its configured limit (default120seconds); it is not the2second entry
standard. Default polling cadence and every execution freshness/risk limit are
unchanged. `-CryptoRead` on the updated check script performs a bounded local
provider probe without starting Atlas; see `crypto-snapshot-readiness-check.md`.

## Strictly non-executing observer prototype

`app.asset_modules.grok_observer` accepts immutable typed snapshots for
Warrior Momentum and Quick Scalper only. Its output is an immutable WATCH/PASS
observation record with `execution_authorized=false`. It has no order service,
controller, entry producer, master dispatch, management callback or config writer.
It is not wired into desktop composition or enabled by an environment flag.

Both sides require explicit timestamps, positive finite uncrossed prices,
known provenance and age in [0, 2] seconds. Responses arriving after expiry are
recorded as expired, not actionable advice. Baseline fingerprints must match the
caller-supplied frozen identity; the prototype does not independently attest the
workstation's current files or settings.

The client receives only whitelisted quote fields, owner and baseline identity,
not credentials, account details, positions or private performance. No tools are
supplied, `store=false`, output is bounded by the existing transport, trials cap
at twenty attempted requests and each owner has a sixty-second cooldown. These
are request limits, not a guaranteed dollar ceiling. Provider failures, malformed
or order-shaped responses produce status records without fallback actions.

The default check uses two synthetic fixtures and a fake local transport through
the real request serializer. It makes **zero external requests and zero orders**.
It cannot demonstrate API entitlement, latency, useful strategy advice or
improved profits. A quote alone is insufficient strategy context.

Before an online observation trial, add a separate process consuming sanitized
read-only recorded/streamed evidence, a bounded local observation log, strategy
context captured before the decision, token-usage accounting and a frozen
baseline manifest. Keep it out of GUI, quote/protection and native entry paths.
Do not introduce a candidate unless the deterministic baseline already produces
it; disabled/quarantined strategies remain disabled. Pair later outcomes by
original owner/event identity and keep those records private.

Follow-up: `grok_observer_input` now supplies the bounded read-only snapshot-file
adapter. It validates all input before the first injected observer call. No
file tailer, automatic data export, online client construction, usage accounting
or desktop binding has been enabled. Those remain prerequisites for a live trial.

`ATLAS_GROK_PAPER_ENTRIES_ENABLED` is the existing **order-review path**.
It must remain false. Turning it on is not an observer trial. There is no new
observer-only desktop switch in this change.

## Subscriptions verified from official public pages

Prices below were checked October 24, 2026, in USD. Account-specific entitlement
and checkout price are not established by public documentation.

| Service | Official terms checked | Need for this stage |
| --- | --- | --- |
| xAI API | Grok 4.7 standard global short-context pricing: $2/million input tokens and $6/million output tokens; long context at 200k tokens uses higher rates | No paid access for offline fixtures. An API account, model access and billing are needed only for a later online observer. Grok website/app billing is separate. |
| Webull OpenAPI crypto | No additional crypto data subscription; snapshots up to20 symbols,1request/second/AppKey | Do not buy a crypto quote package to fix a parser, timestamp, coverage or SDK error. Authenticated API permission still needs local verification. |
| Webull OpenAPI equities | Nasdaq Basic L1 or TotalView L2 for non-display API usage; app/desktop subscriptions are separate | Check the existing API entitlement and the actual required quote fields first. Public pages reviewed did not expose a verifiable account-specific price; inspect OpenAPI Advanced Quotes before buying. L2 is not automatically required for an observer. |
| Alpaca Basic | Free; real-time IEX equities,30 websocket symbols,200historical requests/minute; most recent15minutes restricted for historical queries | Sufficient for offline work with existing history. Past SIP-history access already demonstrated in this project does not itself prove current real-time SIP entitlement. |
| Alpaca Algo Trader Plus | $99/month; all US stock exchanges, unlimited equity websocket symbols,10,000historical requests/minute; real-time OPRA options | Conditional on actually using Alpaca consolidated live equity data. Not required to repair Webull crypto or run offline observer fixtures; do not duplicate an adequate existing feed. |

No new X/news, options, futures, depth or Grok consumer subscription is required
for the prepared quote-only offline trial. The existing Grok transport sends no
Web/X search tools; Grok must not be treated as an exchange quote feed.

If a later online trial is selected, use existing xAI account access and retain
credentials locally. Review available prepaid balance, leave automatic top-up
off, and keep invoiced spending at zero for a bounded pilot. The official billing
page documents these controls; no funding amount is authorized by this review.
Example only:20requests of2000input+500output tokens at2/6 dollars per million
would cost $0.14 before extra reasoning, tools, taxes or other usage. Actual
consumption must be measured; this is not a cap or a quoted subscription fee.

Official sources:

- <https://docs.x.ai/developers/pricing>
- <https://docs.x.ai/developers/models/grok-4.7>
- <https://docs.x.ai/developers/faq/accounts>
- <https://docs.x.ai/console/billing>
- <https://developer.webull.com/apis/docs/market-data-api/overview/>
- <https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes/>
- <https://developer.webull.com/apis/docs/reference/crypto-snapshot/>
- <https://docs.alpaca.markets/us/docs/about-market-data-api>

## Local operator checks without launching Atlas

Keep `WEBULL_TRADING_ENVIRONMENT=PAPER`, `LIVE_TRADING_ENABLED=false` and
`ATLAS_GROK_PAPER_ENTRIES_ENABLED=false`; keep crypto proposals off in the market
controls. Preserve other settings and all protective-exit infrastructure.
Do not regenerate working API keys just for this check.

Extract `tools/check-weekend-observer.ps1` from the research branch and run it
with `-Repo` pointing to the installed repository. It checks only the three mode
flags, runs observer fixture tests from a disposable worktree and prints synthetic
observations. It changes no settings and makes no market-data or model requests.
It cannot inspect an already running process's environment or certify actual
crypto activation state. If flags are unsafe, it stops with no changes.

Next setup steps: verify those local flags and installed revision; inspect existing
Webull OpenAPI entitlements and displayed price; retain the existing Alpaca keys;
verify xAI model access and usage controls before considering an online test.
Do not purchase subscriptions merely because offline construction succeeds.
