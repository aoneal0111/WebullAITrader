# Native NFI pipeline diagnostic trial

The initial one-hour bootstrap completed without trades. Its permission check
proved only that an ALLOW lease could be accepted. Heartbeats did not establish
candle processing, signal generation, or native entry confirmation.

`tools/prepare-freqtrade-diagnostics.ps1` verifies an existing lab and reuses its
pinned NFI code and license in a new directory. It does not copy the ledger,
mutable cache, entry leases, or controller state. The original lab and Atlas
are unchanged. Preparation makes no network or Grok requests beyond Git fetch.

The printed manual launcher checks the pinned container runtime and starts a
separate Docker project for ten minutes. That process requests public Kraken
market data. It does not contain exchange credentials or make Grok calls.
Both the manual pause marker and diagnostic marker block entry leases. The
launcher stops only this diagnostic container after observation. Existing lab
containers and their protective exits remain untouched.

Optional passive wrapper instrumentation records loop, indicator, signal, and
entry-confirmation stages. It preserves native return objects and rethrows
native exceptions. Diagnostic I/O errors never alter native decisions. The
snapshot contains at most the two whitelisted pairs, fixed stage counters,
row counts, startup count, last candle open time and its age, recent signal
row counts, resolved configuration acceptance, and lease acceptance. No
prices, raw indicators, entry tags, credentials, or exception messages are
exported. Writes are throttled to once per five seconds using atomic replace.
Counters describe the current process and repeated observations; they are
not unique trades or performance statistics.

Interpret `user_data/native-diagnostics.json` as follows:

- Loop counts alone: process ran; pipeline readiness remains unproven.
- Indicators started without completed: inspect native errors and data provider.
- Indicators and signals completed: the strategy evaluated candle frames.
- Compare rows with startup count and latest candle open time with the 5m
  timeframe. Candle open age is not quote age; a 5m candle is not a fresh BBO.
- No recent signal rows: no long signals in that observed window. This does
  not prove an error or lack of profitability over another period.
- Signal rows with false entry-signal outcomes can reflect native freshness,
  last-row, exit conflicts, or other Freqtrade filtering. Inspect further.
- Entry lease rejections are expected: this observation process cannot trade.
  Native NFI confirmation after the lease cannot be tested in this paused trial.

This is a separate-process reproduction, not evidence about the prior process's
in-memory frames. No stop, size, pair list, indicator, or signal rule is relaxed.
Unit tests use synthetic upstream methods and frames. Actual Windows container
execution and public-feed results must be supplied by the operator. This trial
does not validate profitability or live execution.
