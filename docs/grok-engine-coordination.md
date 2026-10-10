# Grok engine and master coordination

Atlas standardizes its model transport on Grok. All roles share `GrokClient`,
the xAI Responses endpoint, JSON schema outputs and the same local model setting.
There is no automatic Gemini/Claude fallback. Old Gemini settings are ignored.

## Delivered wiring

Desktop composition creates one `GrokSuite` when both `XAI_API_KEY` and
`ATLAS_GROK_MODEL` are configured. It supplies the existing Crypto supervisor's
provider and exposes advisers for Warrior, Scalper, Crypto, Options, Futures,
plus a master adviser. Construction and viewing a tab make no API call.
Crypto proposals remain OFF at application startup and the checkbox now
discloses sharing quotes and simulated account state with Grok.

The other advisers and master are callable interfaces, not yet subscribed
trading loops. They do not start workers, intercept existing equity trades,
or turn Options/Futures planning workspaces into executing engines. Crypto's
existing supervisor remains the only connected model proposal loop in this stage.
Its separate paper account is not yet part of the equity controller capital ledger.

## Engine-to-master protocol

An engine creates immutable candidate records with identity, owner, expiry and
JSON evidence from its strategy adapter. An adviser can select up to two IDs
or none; it cannot rewrite the candidate. Candidate intents are:

- ENTRY: new entry plan.
- CANCEL_ENTRY / REPLACE_ENTRY: change an existing entry.
- REDUCE / CLOSE: partial or full exit.
- TIGHTEN_STOP: stronger protection.

Management requires an owning lifecycle and authoritative revision. Order
changes also require the exact order ID. The master receives only the latest
successful recommendations from each engine, with session/request/sequence and
reason. Failed new reviews revoke prior recommendations. Expired recommendations
are omitted. Old request IDs, unknown IDs and duplicate selected IDs are rejected.

The master ranks immutable proposals against supplied account evidence. Its
selection is not admission, a capital reservation, or an executable order. The
remaining dispatcher must recheck ownership, current order/position revision,
quotes, buying power, risk, session and duplicate identity before making changes.
No management candidate is currently dispatched by these new interfaces.
Existing local protection continues independently of AI calls.

Strategies from external repos enter through engine-specific adapters; attach
source/version, declared costs and test evidence to candidate evidence. No repo
is automatically promoted because its author reports good returns. Open positions
retain their policy identity; new policy versions require explicit migration logic.
The Grok client has no tools for code edits, broker access, shell or GUI control.

## Boundaries and configuration

The shared client allows two concurrent requests, one attempt per role per minute,
120 attempts per client lifetime, 15-second HTTP timeout, 64 KiB context/response,
and 2,048 output tokens per request. Failed requests count and are not retried.
Busy requests fail promptly instead of enqueuing old market snapshots. These
limits apply to model advice, not the quote/protection loop. No automatic provider
web search is enabled; news must come from timestamped Atlas acquisition evidence.

The HTTP request uses a fixed HTTPS endpoint, rejects redirects, requests
`store=false`, and never includes the API key in message data. `store=false`
disables response retrieval storage; it is not a claim about all provider retention.
Only safe error codes surface. Price/quantity validation remains in paper execution.

Configure the key locally in the untracked environment file and set
`ATLAS_GROK_MODEL` to an enabled `grok-...` model ID from the xAI console. Restart
Atlas after changing configuration. Do not paste keys into chat or commit them.
Missing configuration produces no AI calls. This stage neither purchases API
credits nor probes the key.

Primary API references reviewed 2026-10-10:

- https://docs.x.ai/developers/rest-api-reference/inference/responses
- https://docs.x.ai/developers/model-capabilities/text/structured-outputs

Tests use synthetic/mocked responses only. They verify protocol, expiry, owner,
request bounds and independent protection; they do not establish trading returns.

Verification for this stage: 51 focused tests passed; the broader asset-module
and desktop-layout suite passed 229 tests on Python 3.13.16 with pinned project
dependencies. All model HTTP responses were mocked. Windows verification and
authenticated xAI/Webull calls are still outstanding.
