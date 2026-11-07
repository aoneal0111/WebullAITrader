# Normal desktop crypto observation capture

This opt-in capture uses the visible Atlas desktop and the existing native
crypto observer. Preparation starts no GUI, market client, subprocess, or model.
It changes no `.env`, equity trial, trading ledger, credential or subscription.

`tools/prepare-crypto-desktop-observation.ps1 -Revision <reviewed SHA>` checks
closed Atlas/Grok-intentions processes, refuses tracked edits, tests an isolated
checkout, creates a backup branch, fast-forwards source and prepares a unique
private capture directory. It prints `MANUAL_ATLAS`; only the operator runs it.
PowerShell is not executed by Codex. The generated launcher opens the normal
visible application and restores its process environment when that app exits.

## Explicit observation configuration

The launcher's child environment selects PAPER, live OFF, native crypto observer
ON, shared controller/allocation ON, daily recording ON, and completed-bar
capture ON. Warrior and Quick Scalper entry engines and Grok order review are
OFF. Old execution/equity experiment path variables are cleared for this child;
no prior lease file or budget is edited. Runtime validation refuses any surviving
old lease variable rather than borrowing its authority.

BTC/USD and ETH/USD are the only configured discovery pairs. Existing history
budgets/cadences are bounded (64 bars, four history requests per refresh, maximum
three history symbols as required by the existing runtime validator). Native
crypto intelligence **research** is enabled because it wires the completed-bar
history context sink; bar persistence alone does not activate that history
refresh path. It is deterministic research, not a model or order switch. Catalyst
acquisition is disabled for this capture. The discovery/bar capture outputs go
inside the new private directory.

`ATLAS_GROK_PAPER_ENTRIES_ENABLED` remains false; it is never used as an observer
switch. `NativeCryptoObserver` still rejects an entry-enabled setting, forces its
router disabled and owns no model provider. Existing protective exits remain on
their independent thread and retain the same source requirements. Installed
positions are not deleted or repriced; existing campaign/reconciliation guards
still apply when the desktop is manually opened.

## Recording and limits

A frozen request binds the app source hashes and resolved configuration digest.
Changed code/settings, reused capture directories, missing recording or unsafe
flags refuse startup. The prior V3 equity trial is not activated or renewed.

The recording is limited to **30 minutes from desktop composition** and the
existing journal's 512 MiB/one-million-record bounds. It does not automatically
close Atlas, stop market data, or disable protective management when time expires.
The operator selects Crypto, activates the module manually and closes/deactivates
it normally when finished, subject to the existing open-position guard.

Native worker submissions queue `CRYPTO_NATIVE_OBSERVER_INPUT` with actual
completed input history and the original quote/time. Completed results queue
`CRYPTO_NATIVE_OBSERVER_RESULT`. Status changes queue
`CRYPTO_NATIVE_OBSERVER_OUTCOME`; identical status reasons are coalesced. No
observer HTTP polling, replayed market prices, quote retiming or invented signal
is introduced. The writer retains the existing nonblocking bounded queue;
writer/capacity failures invalidate complete evidence and do not stop protection.

The journal remains local and private. The completed-bar SQLite capture retains
the provider evidence used by the research runtime. It is not a claim to capture
all market traffic, unrequested depth, news, trades or pre-start history.

## Finding the result

The prepared directory gets `recording-session.json` after manual startup. It
points to the exact recording, avoiding searches for a guessed latest session.
Inside that recording:

- `crypto-readiness.json` reports native worker metrics, data-runtime metrics,
  observation/protection thread state and recorder state, updated by the existing
  two-second monitor. No provider or order requests are added by this monitor.
- `journal/` holds the bounded append-only crypto inputs/outcomes along with
  the existing subscribed evidence. Queue loss and capacity failures are explicit.
- `research-summary.json` exists after normal close or recording expiry and adds
  crypto event counts and final native readiness. Crypto evidence is distinguished
  from the equity engine-evidence status.

Share the small readiness and summary files for review. Raw prices/history and
ledger outcomes remain private; no trading performance is published. A healthy
capture proves observed data/lifecycle behavior only, not profitable signals,
fills or Grok execution readiness.

## Verification

Focused regression includes native worker evidence reaching the actual journal,
no entry/allocation authority, sink failure with continued protection, real
inert desktop composition with its history sink and observer bound, frozen
source/configuration rejection, one-use requests and closed summary receipts.
No actual API or GUI is started by these tests.

Local validation completed with 1,802 passing asset-module, crypto-research,
desktop-composition and workstation tests. PowerShell is verified on the
operator workstation by the installer's focused tests before source merge; it
was not executed in the local Linux test environment.
