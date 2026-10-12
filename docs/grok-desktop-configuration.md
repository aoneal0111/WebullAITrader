# Grok desktop PAPER entry configuration

The desktop can now construct and bind the previously tested native Grok entry
producer. It remains disabled by default. This is Warrior/Quick Scalper PAPER
entry review plus a master ranking adviser; deterministic native exits,
protective stops and risk gates continue independently. It does not activate
options or futures execution, rewrite running code, or prove profitability.

## Explicit settings

Set these locally in the launch environment or the untracked project `.env`:

```dotenv
WEBULL_TRADING_ENVIRONMENT=PAPER
LIVE_TRADING_ENABLED=false
ATLAS_PAPER_CONTROLLER_ENABLED=true
ATLAS_GROK_PAPER_ENTRIES_ENABLED=true
WARRIOR_FORWARD_PAPER_ENABLED=true
QUICK_SCALPER_ENABLED=true
XAI_API_KEY=<your local xAI API key>
ATLAS_GROK_MODEL=<your available Grok model supporting structured outputs>
```

At least one of Warrior and Scalper must be enabled. Preserve other broker,
market-data, account and risk settings; this block is not a replacement `.env`.
The existing GUI loader reads `.env` with process-environment precedence. Keys
stay in the transport and do not enter candidate messages, configuration check
output or repository files. Do not paste keys into chat or commit them.

Enabled entry mode requires an actual PAPER desktop runtime and the controlled
PAPER service. Invalid mode, missing controller/engines, or missing key/model
stops composition with a configuration error before runtime resources are
created. No silent synchronous-entry fallback is allowed for a requested Grok
entry configuration. Disabled mode preserves existing native entry behavior.

## Provider and lifecycle

Composition constructs one Grok suite without contacting the provider. The
engine advisers, master and existing Crypto proposal provider share that client;
Crypto remains independently activated through its existing controls. Worker
creation for reviewed entries occurs only when the native branch offers a
candidate. No model runs on the quote/protection or GUI thread.

Stop revokes and reconciles the producer as before. A subsequent start creates
fresh review ownership only after the old producer has closed and resolved all
native receipts. The shared provider and its limits persist for the desktop
application lifetime: two concurrent requests, a 60-second role cooldown, and
120 total requests, including failed requests. These are request bounds, not a
guaranteed dollar budget. A selected entry normally uses engine and master
requests; not every scanner candidate triggers a request. Rate-limited or failed
reviews do not authorize a fallback buy.

## Offline check

Run without launching Atlas:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.grok_configuration_check
```

This reads local configuration and constructs the provider only; it does not
start workers, send a request, open execution storage or place an order.
`provider_constructible` validates local syntax/presence only.
`credentials_validated_online` is always false: API key entitlement, model access
and provider latency require a later explicit online check. API usage can incur
provider charges when an enabled runtime submits candidates.

The existing transport uses the xAI Responses API with structured output and
`store=false`. References checked October 12, 2026:
- https://docs.x.ai/developers/rest-api-reference/inference/responses
- https://docs.x.ai/developers/model-capabilities/text/structured-outputs

Verification includes full desktop composition with zero requests, shared Crypto
provider identity, invalid configuration before provider construction, fresh
review ownership on restart, and the offline check's secret-free output.
