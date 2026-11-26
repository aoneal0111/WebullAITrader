# Completed crypto bars and quote alignment

Run `tools/check-weekend-observer.ps1 -CryptoBars` to verify the data path using
the existing native crypto signal rules. This is a separate read-only probe from
a disposable source checkout. It does not launch Atlas or activate crypto/Grok
entries, modify the installed strategy/configuration, construct a ledger or
controller, submit a proposal, place an order or call xAI.

The bounded experiment takes about two minutes:

1. Discover BTC/USD and ETH/USD with the existing market-data identity.
2. In each of three cycles, request up to64 native one-minute bars per pair,
   then normalize them using the existing completed-bar normalizer. Freeze each
   bar batch's observation/cutoff time at actual history-response receipt.
3. Request fresh snapshots **after both history responses**, then use the actual
   snapshot-receipt time as the joint decision cutoff.
4. Run the existing `crypto_history_adapter.adapt` against each pair. If its
   input is ready and data-quality checks pass, call the unchanged pure
   `crypto_signals.propose` function with compiled default `CryptoSignalSettings`.
   Record only its reason and whether a hypothetical candidate existed. Every
   returned candidate is discarded; no dispatcher, worker or supervisor starts.
5. Repeat at65second cycle-start intervals so a healthy feed must advance the
   latest completed bar. There are at most six history requests and three
   snapshot requests, plus one discovery page. Provider retries remain off and
   the CLI retains its five-second SDK timeout and existing request pacing.

The normalizer excludes unfinished candles. At most one unfinished row per
pair/cycle is allowed for a clean result. No snapshot-generated candles,
forward filling, timestamp shifting or silent gap repair is used. More than64
history rows fails the bound. The report includes a safe inventory of provider
field names so schema failures can be investigated without exposing response
bodies or credentials.

The existing adapter requires22–48 completed bars, ordered contiguous one-minute
history, the latest close within one minute, matching pair/provider identity,
and bid/ask no more than two seconds old with no future timestamps and a quote
timestamp at or after the latest bar close. This probe additionally rejects
malformed/duplicate rows, off-grid minute timestamps, completed-bar revisions
observed across cycles, and a latest completed bar that fails to advance.

History is never re-stamped with a quote's earlier source time. Its availability
is checked at the decision cutoff when both inputs were actually known. A
minute-boundary publication delay can correctly produce a nonpassing sample;
that remains visible rather than granting a later bar retroactively.

`BOUNDED_BAR_QUOTE_ALIGNMENT_PASSED` requires all six pair/cycle inputs to pass,
both requested quotes only, all three cycles, at least130seconds of monotonic
elapsed time, no detected clock step and no terminal error. The scheduling
deadline is180seconds plus an in-flight SDK call. Provider or normalization
errors preserve a partial, sanitized result instead of retrying or activating a
fallback path. Clock checks measure wall/monotonic progress, not absolute NTP.

A normal `NO_CONFIRMED_SPOT_BREAKOUT` signal assessment can accompany a passing
data result. Data readiness does not require a BUY. Conversely a hypothetical
`PROPOSED` assessment is still not order permission or strategy qualification.
No profit, fill or account-performance measurement is performed.

The small private Desktop JSON contains normalization counts, bar/quote ages,
adapter statuses, bar advancement, revision counts, assessment reasons, a settings
fingerprint and hashes of the exact source files. It excludes raw prices,
candidate terms, credentials, account data and trading performance. A failure
still produces the report; send that file and console output for review.

All local PAPER/live/Grok guards remain in effect. The check does not certify a
running process's actual activation state. Keep crypto proposals off. The same
strategy settings and code hashes establish this probe's baseline; they do not
attest unobserved custom settings in a different installed runtime.

Passing this input check does not validate the worker/ledger round trip,
continuous data, every pair, profitability or the online Grok observer. Those
remain separate readiness questions. No subscriptions or API budgets change.
