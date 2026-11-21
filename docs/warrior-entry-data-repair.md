# Warrior PAPER quote routing and startup recovery

The November 16 review contains two new Scalper closures (OFAL -$4.29 and
GDHG -$2.17), no Warrior fills, and substantial quote freshness failures. It
also shows a 16m47s gap before broker connection. These observations do not
prove the strategy's historical entry behavior has been restored.

## Confirmed code defects addressed

In an explicitly configured PAPER operation with the existing stream-clock
policy, only Scalper used PaperStreamQuoteSource. Warrior and the controller
used REST-only confirmation, even when fresh subscribed book evidence existed.
They now share the existing thread-safe source. Grok directed entries read the
same controller source. Invalid/stale/untrusted stream data still falls back
to REST; downstream per-engine age, risk and authorization checks remain.
No provider timestamp is changed and no additional network request is added.
Other operations, including LIVE, keep their original source.

Startup restored minute bars by materializing every capture record, including
unrelated diagnostics, before broker connection. Recovery now streams only
minute bars and observation-session identity boundaries in batches of 256.
It maintains the newest 120 bars per symbol during recovery, including when
bars arrive out of timestamp order. Direct fingerprints and legacy inherited
START/END attribution are preserved. Historical rows are not deleted. The
component duration warrior.startup.bar_recovery is recorded to distinguish
this work from other observer initialization. This fixes an unbounded startup
path; it does not establish that this alone caused the entire observed delay.

## Verification and limits

Offline integration verifies that the actual desktop event observer feeds
Warrior and its controller without needing a REST call when stream evidence
is fresh. Recovery tests include 3,000 irrelevant diagnostics, reversed bar
arrival, foreign configuration bars, and unbound legacy bars after END.
Existing quote rejection, library, Grok, partial-fill, profit harvest, and
protective stop tests run in the installer before a fast-forward update.

The updater requires Atlas closed, creates a backup branch, changes source
only, and leaves the existing launcher, experiment spec, ledgers, strategy
parameters, Grok review policy and crypto workers alone. Nothing starts.

No claim is made that historical Warrior trading behavior or profitability
is restored. No real provider connectivity is tested. The 561 reconnect
failures, Grok abstentions and shutdown exception require their own tracing;
this repair does not suppress those errors or force entries.
