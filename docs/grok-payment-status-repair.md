# Grok payment-required status and recovery

HTTP 402 responses previously contributed to the client's HTTP error counters
but were reported to Warrior as generic provider unavailability. Continuous
Director operation could therefore keep making new unsuccessful requests every
ten seconds while the API account needed attention.

The shared Grok client now reports the fixed category PAYMENT_REQUIRED and
numeric status 402 without inspecting or retaining the provider error body.
Warrior's control status becomes GROK_PAYMENT_REQUIRED and includes the required
action CHECK_XAI_API_TEAM_BILLING plus a remaining retry delay. The status is
available through the existing read-only Warrior entry status command.

After observing 402, the client holds new requests across its roles for five
minutes, before durable attempt reservation or network dispatch. At expiry,
one freshly requested review may check recovery; a second concurrent recovery
request stays blocked. There is no background probe, stored-context replay or
automatic retry of a rejected decision. Normal current-context, quote,
authorization and model approval checks still apply.

A valid response to that recovery request clears the payment warning. Another
402 or any unsuccessful recovery response starts another five-minute wait.
The existing 429 Retry-After handling remains in force. A restart creates a new
client and can check the provider again; backoff is in process memory.

This change cannot add API credits, change the API team's spending limit, or
verify the external account's billing. HTTP 402 identifies a payment-required
response; the exact account cause requires checking the API team's console.
Nothing here bypasses Grok review, changes strategy parameters, switches live
trading on, stops protection, or restarts crypto workers.

Run tools/repair-grok-payment-status.ps1 with Atlas closed and an exact revision.
It verifies the persistent PAPER spec and runs offline tests in a detached
checkout with a short Windows check path, then backs up and fast-forwards the
installed source. No provider calls or orders are made by installation.
