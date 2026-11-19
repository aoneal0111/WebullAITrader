# Continuous Warrior Grok cost controls

This opt-in overlay reduces background reviewing and adds a persistent local
budget without changing the strategy detectors, entry economics, stops, targets,
model choice, or continuous PAPER operating spec. Existing operations without
grok-cost-policy.json retain their previous behavior until explicitly upgraded.

## Default policy

- $1 per UTC calendar day in estimated request reservations.
- Director background calls may consume at most 25% of that allowance. Native
  entry review can use the remaining capacity, including unused Director capacity.
- At least 60 seconds between Director batches, and 300 seconds between attempts
  for the same symbol. Up to four symbols remain eligible per batch.
- Unchanged background evidence is skipped until a 30-minute heartbeat. Material
  changes include a new completed bar, a different session, position changes,
  or a last-price change of at least 0.5% from the last successful review.
- Director yields while native entries or acknowledgements are pending.

All eligible subscribed symbols remain in the fair watchlist; budget availability
limits how many can receive reviews. A remembered background review is only used
to suppress unchanged analysis. BUY/EXIT answers and authorizations are never
cached for reuse. Native entry reviews still require fresh current quotes, risk
approval and a new valid model answer. A depleted budget can prevent further
Grok-dependent entries until the next UTC day; native position protection remains.

## Accounting and limits

Reservations and call records share one SQLite transaction and survive restart.
The UTC day is fixed when the request is reserved. Failures, unknown outcomes and
requests without usable usage keep their full reservation. Old requests from
before enabling this policy are not retrospectively estimated or debited.

The estimate uses UTF-8 request evidence bytes plus 8,192 bytes of framing
allowance as a conservative input-token estimate, and the configured maximum
output tokens (at most 2,048). It assumes no caching discount. Only the exact
models grok-4.5, grok-4.6 and grok-4.7 are priced; unpriced models or oversized
requests are refused before durable call reservation or transport.

Global endpoint rates were verified on 2026-11-19 at
https://docs.x.ai/developers/pricing: $2 per million input and $6 per million
output tokens for these models below the long-context threshold. Estimated input
is capped at 128,000 tokens. This is a deliberately conservative reservation,
not an exact tokenizer, invoice reconciliation, or guarantee about future prices.
No refund is inferred from cumulative token counts because concurrent calls make
per-request attribution unsafe.

The limit is local to this persistent Warrior operation. It does not cover other
applications, API keys, separate operations or charges outside these calls.
Use the provider's account spending limit for an account-wide invoice cap.
Status shows reserved estimates, remaining allowance, the Director share and the
UTC day under warrior_control_status.requests.cost_budget. Historical operation
attempt counts remain cumulative and are not today's spending totals.

## Installation

Close Atlas normally. Run tools/repair-grok-cost-controls.ps1 with an exact
revision, PaperDirectory and optional DailyBudgetUsd (default 1.00). It validates
the existing continuous operation and budget amount before testing, backs up and
fast-forwards source, then writes the explicit cost-policy file. Changing an
existing cost policy preserves a backup. The operating spec, wallets, ledgers,
API credentials and model choice are retained. No trading process is launched.
Restart is required to load policy changes; deleting the optional policy file
would remove these controls on the next start. The policy is not tamper protection.

## Selecting native-only versus Grok-assisted startup

This is a stopped-only PAPER startup switch, not a hot swap of in-flight entry
reviews. After a normal Atlas shutdown, run
`tools/Set-Atlas-GrokMode.ps1 -Mode OFF -PaperDirectory <existing-paper-directory>`
to create the existing `STOP_GROK` marker. The dedicated Grok Warrior bind
function detects that marker **before** constructing the Grok client or binding
its entry reviewer. On the next startup, Warrior uses the existing native
entry submitter, and the native scalper remains independent of Grok. No xAI
calls should be made by this dedicated controller in OFF mode. Other separately
configured Grok observers should be stopped and independently disabled as needed.

With Atlas stopped, use `-Mode ON` to remove the marker. The next startup
installs the Grok Warrior review path again if provider configuration is valid.
The command does not start Atlas, modify order ledgers, cancel protective
orders, change strategy policy, bypass risk checks or change LIVE settings.
Do not remove the marker while Atlas is running: a mid-session change can
leave a previously bound Grok reviewer in place until restart. Pending or
uncertain acknowledgements must be reconciled through normal shutdown and
restart. Switching OFF does not automatically fall back to native execution
for an already running Grok reviewer.

This is the first safe operating-mode boundary. An interactive desktop GUI
control must be built separately after a reliable shutdown/reconciliation and
status model exists. Do not label this initial implementation a live toggle.
