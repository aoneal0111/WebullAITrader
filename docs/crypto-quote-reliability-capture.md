# Five-minute crypto quote reliability capture

This is a bounded market-data observation tool, not an Atlas runtime start,
strategy validation, Grok test or crypto activation. The PAPER strategy baseline,
entry freshness limits, risk and protective-exit paths are unchanged.

Run the updated `tools/check-weekend-observer.ps1` with `-CryptoReliability`.
It performs the existing mode guards and offline fixture tests, then runs the
data-only probe from a disposable checkout. It uses existing local scoped Webull
market-data settings and never changes credentials or the installed source tree.

The fixed experiment:

- Discover only BTC/USD and ETH/USD, at most one instrument page.
- Up to60 snapshot attempts at5second start intervals, about5minutes total.
- A300second scheduling deadline; an in-flight SDK request can finish after it.
  The CLI retains its5second SDK timeout and disables provider retries.
- No new catch-up bursts after slow responses. Each attempt starts at least
  five seconds after the preceding slot's start under a normal monotonic clock.
- Defer requests for at least60seconds after a429; retain deferred slots.
- Stop early on permission, unsupported-pair or business-request errors. Retain
  transient transport/malformed-data errors and continue at the normal cadence.
- Check unique full two-pair coverage, bid/ask presence and quote age in[0,2]
  seconds after each response. Unexpected symbols also fail a sample.
- Compare wall-time progress against monotonic progress; differences greater
  than250ms are reported as clock discontinuities and prevent a clean result.
  This detects clock steps, not absolute synchronization against an NTP source.

The private Desktop JSON contains all bounded sample outcomes, request durations,
age ranges and per-pair counts. It contains no credentials, raw prices, account
balances or trading performance. There are zero order and Grok requests.

`BOUNDED_WINDOW_FRESH` requires60 fully fresh complete samples, no provider or
coverage failures, no deferred slots, no clock discontinuity and no early stop.
Otherwise the report retains the failed samples and returns a nonpassing status.
Recovering after a failure never erases the failure from this window.

Recovery is recorded only when a genuinely bad observation/provider error is
followed by a complete fresh sample. The live capture does not deliberately
break the feed or inject failures. A window with no bad samples reports
`NO_RECOVERY_OBSERVED`; it cannot establish real-provider recovery behavior.
Tests exercise recovery/backoff with fake time and injected failures offline.

The full sample list is bounded to60 records and can be sent for review. The
script opens Explorer on the small file, including when the capture fails.
Ctrl+C delivered to Python is handled as an interrupted, nonpassing capture and
preserves completed samples where normal Python interruption is available.

This does not verify continuous quotes between polls, minute-bar continuity,
other pairs, overnight reliability, engine dispatch, fill quality, profitability
or the xAI observer. Do not enable crypto proposals or Grok order review based
solely on the result. No account subscriptions or provider-side budgets change.

Direct equivalent after obtaining the reviewed source:

```powershell
& $Python -m app.crypto_research.readiness --repo $Repo --output $NewOutput --online-read --reliability
```

Offline checks use a fake clock/sleeper and fake data-only providers, including
success, missing/duplicate symbols, old/future quotes, absent bid/ask, actual
bad-to-fresh transitions, rate-limit cooldown, terminal permission failure,
clock steps, deadline termination, interruption and CLI mode dispatch. No
five-minute real wait or authenticated provider call is needed for these tests.
