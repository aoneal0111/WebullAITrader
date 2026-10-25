# Native crypto desktop observer

This update connects the existing deterministic crypto signal subprocess to the
desktop market lifecycle as an opt-in observer. It does not qualify the strategy
or enable entries. The current range/EMA rules, 48-bar worker contract, 3R target,
fees, slippage and risk budgets are unchanged. Warm-up/exit-strategy experiments
remain separate research work.

## Configuration and ownership

`ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED` defaults to false. When explicitly true,
composition chooses `NativeCryptoObserver` instead of the Grok proposal
supervisor. Exactly one crypto supervisor owns the market lifecycle. The native
observer has no model provider and its entry setter rejects true; the existing
crypto proposal checkbox is disabled and explains that native observation has
no entry authority. A missing model key is not shown as an error for this mode.
No `ATLAS_GROK_PAPER_ENTRIES_ENABLED` switch is repurposed.

Before opening the crypto ledger, native mode requires:

- PAPER environment and disabled live trading;
- the existing desktop PAPER controller and shared crypto allocation enabled;
- an actual `ControlledPaperTradingService` and crypto research runtime;
- Grok order review disabled.

Invalid prerequisites fail before crypto ledger creation. Existing allocation
binding/campaign/exposure checks remain intact. A legacy standalone position is
not silently migrated, adopted or deleted. No configuration or credentials are
edited by the update helper. Enabling a new flag is a separate explicit runtime
configuration choice, not a side effect of installing code.

## Data-to-observation path

On normal manual crypto-module activation, the data runtime retains its existing
market-data ownership. The observer reads cached completed bars and current
research quotes; it performs no new HTTP polling of its own. The existing
`crypto_history_adapter.snapshot` validates provider/pair ownership, causal
availability, M1 continuity, current bars and a quote no older than two seconds.
Only BTC/USD and ETH/USD are observed in this initial scope.

The observer's owner thread constructs, starts, polls and closes
`CryptoSignalWorker`. The subprocess remains pure, and the existing
`CryptoWorkerController` verifies its response and re-evaluates with the parent's
current quote. The shared allocation router is forced disabled before polling
or submission. A qualifying research proposal therefore reaches
`ENTRIES_DISABLED` rather than a reservation or fill. There is no UI or model
route that enables native entries.

One assessment per symbol/completed-bar geometry is submitted. This is a bounded
observation cadence, not evidence of tick-by-tick entry responsiveness or a win
ratio. A completed observation records a bounded OBSERVE decision with native
policy/reason, not a simulated order. Metrics retain submitted/completed counts,
qualifying signals held, input waits, worker failures and the latest outcome for
each of the two symbols. Missing history and rejected quotes remain explicit.
Neither a PASS nor a hypothetical signal is reported as a profitable trade.

## Lifecycle and protection

Construction is inert: it starts no thread, subprocess, market subscription or
model call. The usual market-module start/stop controls own activation.
An independent protection thread continues the existing local stop/target and
allocation-reconciliation path. A failed/stalled signal worker does not stop
that thread. Unexpected native-worker/input failures stop signal processing
without an automatic worker restart. Explicit module close/start creates a new
worker session, with entries still disabled.

A module with open exposure remains under the existing close/deactivation guard.
Protective exits can produce local PAPER fills on existing positions; the claim
is zero new native BUY authority, not that protection stops executing. Protection
still depends on available valid quotes and the existing ledger locks; it is not
an exchange-hosted stop or a hard real-time guarantee. No broker order path is
added by this update.

## Installation and Monday's capture

The existing `tools/apply-premonday-repairs.ps1 -Revision <reviewed full SHA>`
installer now includes native observer, desktop composition and shared-allocation
Windows checks when these tests are present. It retains its closed-Atlas guard,
tracked-edit refusal, backup branch, ancestor/fast-forward checks and no-reset
failure behavior. It prints only three non-secret effective booleans:
`PAPER_CONTROLLER_ENABLED`, `CRYPTO_SHARED_ALLOCATION_ENABLED`, and
`CRYPTO_NATIVE_OBSERVER_ENABLED`. It does not enable them or launch Atlas.

Installing app/config changes invalidates the old capture source/config receipt.
After Windows checks, the installer prepares a fresh source copy under
LocalAppData and prints its new `LOCAL_START_SCRIPT`. Use that newly printed
launcher for the regular-session equity capture. Enabling native mode afterward
changes configuration and requires re-preparation again; the receipt guard will
refuse a mismatch instead of silently changing the trial baseline.

No third-party framework dependencies, subscription purchases, xAI calls or
credential entry are involved. The installed Windows checks and actual market
observation remain pending until the operator runs the update and manually
starts the desired market module. No Atlas GUI was launched by Codex.

## Validation

The combined asset-module, crypto-research, composition, workstation-composition
and configuration test run passed **1,565 tests**. The new scenarios verify:

- inert/default-off construction and strict refusal to enable native entries;
- real subprocess ownership on its polling thread, qualifying proposal blocked,
  zero new positions/fills/reservations and no model requests;
- no added provider-history requests and same-bar duplicate assessment suppression;
- stale/future quotes and discontinuous history refused before worker submission;
- real subprocess termination while an existing synthetic PAPER position keeps
  protection, exits on a subsequent target quote, and settles shared capital;
- invalid native prerequisites refusing before ledger creation;
- mutually exclusive desktop owner selection and manual module lifecycle;
- disabled native entry UI with no irrelevant Grok credential prompt.

A full source-copy preparation with PAPER/live-off/Grok-review-off and the native
flag enabled also passed its offline compatibility check without GUI/network
execution. These are synthetic/local correctness checks, not measurements of
live reliability, profitable performance or a newly approved strategy.
