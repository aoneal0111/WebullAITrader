# Warrior confirmation clock repair

Warrior could treat a future provider timestamp as age zero in the sidecar,
then offer the future-dated signal to native review. Native review correctly
refused it. A fresh quote reassessment could also retain the earlier future
candidate through discovery's out-of-order guard.

Discovery now uses the local evaluation boundary for an observation whose
provider event time is ahead. The original observation and provider component
timestamps remain unchanged. An explicit check of those original times forces
fresh quote confirmation before entry. The confirmed snapshot is a new local
observation: prices, quote and trade timestamps are taken from the confirmation,
and setup selection, generation, liquidity, risk and native review run again.
A missing, stale or still-future confirmation cannot authorize an entry.

The native review zero-future rule, two-second quote rule and review deadline
are unchanged. This does not reuse a pending review or turn an abstention into
an approval. Scalper, crypto, Grok policy, stops, targets and strategy parameters
are unchanged.

Regression cases exercise provider leads of 65 and 1,164 milliseconds, valid
confirmation, missing confirmation and future confirmation through taxonomy
selection, native review, controlled PAPER execution and owned protection.
Tests use deterministic model responses and PAPER fixtures; they do not contact
providers or demonstrate profitability.

The motivating workstation capture classified 17 gate records as future at
evaluation and left 41 without a unique evaluation pairing. Those 41 are not
explained by this repair. Ten completed native model responses abstained and
four other calls were unavailable or rate-limited. These remain separate
outcomes, not evidence that native review should be bypassed.

Run tools/repair-warrior-confirmation-clock.ps1 with Atlas closed and the exact
published revision. It checks a detached checkout offline before a backup and
fast-forward update. It retains the persistent operating spec and ledgers and
does not launch Atlas or restart crypto. A fresh workstation recording is
required to verify external entry flow after installation.

## Windows installer check paths

The installer uses a short, unique `LOCALAPPDATA/AtlasChecks/wc-<id>`
checkout and test root. Nested recording fixtures under the former verbose
repair directory exceeded legacy Windows path limits, causing missing context
files and recording initialization failures before installation. The short root
keeps those fixtures below the limit without changing machine-wide settings,
skipping tests, or altering recording paths used by the actual Atlas operation.
