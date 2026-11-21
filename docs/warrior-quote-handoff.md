# Warrior quote handoff after REST

Warrior previously checked its subscribed stream cache only before requesting a
REST confirmation. When fresh book and trade events arrived during that request,
it still returned the REST result. A stale REST result could therefore reject an
assessment even though a complete fresh stream snapshot was now available.

The source now checks its cache again after REST returns. This applies only to
`WarriorPaperStreamQuoteSource`. Both the original provider book and trade times
must satisfy the unchanged two-second, zero-future Warrior selector. The existing
receipt-age validation also remains. No REST/stream fields are combined, no
provider timestamp is changed, and there is no second provider request. Incomplete,
untrusted, delayed, retained-price-only, stale or future stream evidence leaves
the REST result in place for normal validation. REST exceptions still propagate.
Scalper retains its existing selector and timing behavior.

The returned quote's observer provenance follows the chosen source. A decision
made using the post-request stream snapshot is not attached to the discarded REST
request. `rest_requested` counts the real request; `stream_used` counts the chosen
stream result; `rest_replaced_by_stream` identifies this handoff.

The source-bound closed-journal review motivating this change verified 608,431
records. It found 17,093 Warrior assessments: 8,965 stream and 8,128 REST. Of the
REST assessments, 7,903 had bid/ask ages over five seconds. At 170 REST assessment
points, the reconstructed recorded stream book and trade were both within two
seconds. This is evidence of an opportunity to recheck, not proof of 170 missed
entries: subscription membership, concurrent event timing and all later setup,
risk and Grok decisions still matter. The review also found zero volume-only
snapshots, so the earlier retained-price correction is not established as the
cause of that run's rejections.

This change cannot make stale provider data fresh, supply missing bars, qualify
setups or guarantee entries or profit. Strategy parameters, Grok policy, order
sizing, stops, targets and profit management are unchanged.

## Installation

With Atlas and observers closed, extract and run
`tools/repair-warrior-quote-handoff.ps1` from the exact reviewed commit, passing
`-Repo`, `-Revision` and the existing `-PaperDirectory`. The installer checks the
continuous PAPER spec, runs offline regression checks in a detached checkout,
creates a backup branch and fast-forwards only. It leaves Atlas stopped and does
not change crypto workers, the operating spec, credentials or ledgers. It makes
no provider, Grok or order requests.
