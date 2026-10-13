# Read-only recorded crypto signal replay

`python -m app.asset_modules.crypto_signal_replay` pairs a saved completed-bar
capture with the existing crypto research quote JSONL file. Each selected quote
is assessed at its declared `decision_cutoff`, using only bars durably captured
by then. Replay never uses the replay machine's current time to make old evidence
eligible. Research quote flags remain ineligible for execution.

The adapter retains its checks for ownership, provider, completed contiguous M1
history and a matching quote no more than two seconds old. READY frames are sent
to a real `CryptoSignalWorker` subprocess. The worker starts lazily only when a
frame is ready. Its entire deterministic response is validated by the existing
parent protocol. No ledger, entry router, AI provider, broker or execution permission
is constructed. Source files are read-only.

Quote input uses Atlas's current research record schema: asset/pair identity,
provider symbol, timestamp, declared decision cutoff, price, bid/ask and research
flags. Unrelated feature, score and catalyst fields do not affect this initial
range/EMA candidate. Invalid or unsupported records are counted and skipped;
raw data or exceptions containing input contents are not printed.

Nondecreasing selected quote cutoffs are checked within the current batch.
Duplicates use provider identity, source/cutoff timestamps and quote prices.
Signal IDs are counted separately from proposed assessments: repeated observations
of one completed-bar signal are not multiple trades. Deduplication and chronology
state are bounded to a single invocation; resumed batches are independent and
must not be summed as unique trades or portfolio performance.

Default limits are 1,000 scanned lines and 100 worker assessments, with hard caps
of 10,000 lines and 100 assessments. Each quote record is capped at 16 KiB; an
oversized line stops replay. Output retains at most 20 assessment samples. There
are no checkpoint writes or automatic retries. `next_offset` is the exact byte
boundary after the processed records; `--offset` resumes there. Offsets must follow
a complete newline-terminated record. A trailing partial record is left unread
for a later invocation after the writer finishes it. Do not resume an offset after
replacing, truncating or rotating the quote file.

## Evidence limits

The quote log records a declared decision cutoff, not a durable file-write time.
This basis is explicit in the report. The bar database separately preserves first
durable availability. These are recorded snapshot assessments, not proof of
executable bid/ask provenance, fill latency or live performance. The bounded
file-order sample is not representative strategy performance. There is no P/L,
portfolio model, win rate, policy promotion or profitability conclusion.

The command does not activate capture or a real-time feed. Missing capture/log
files produce MISSING_INPUTS without creating them. No completed bars, stale quotes
and other readiness failures remain visible in status_counts. A growing live file
may yield a different bounded sample later; this tool does not claim an immutable
whole-file dataset snapshot.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-replay-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_signal_replay.py `
    tests/asset_modules/test_crypto_history_adapter.py `
    tests/crypto_research/test_bar_capture.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto replay tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_signal_replay --synthetic
if ($LASTEXITCODE -ne 0) { throw "Synthetic crypto replay failed." }
```

The synthetic fixture has four recorded observations, two worker assessments,
one unique signal, one duplicate quote and one observation before bar capture.
It makes zero network requests and orders. It does not establish actual data
coverage or profitability.

## Existing captured inputs

Without `--synthetic`, defaults use the configured crypto discovery JSONL path
and its sibling `crypto-completed-bars.sqlite3`:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_signal_replay `
    --symbol BTC/USD --max-scan 1000 --max-assessments 20
```

Explicit `--bars` and `--quotes` select other existing files. `--offset` accepts the
previous bounded report's next_offset for the same unchanged quote file. Default
capture is still off unless selected on a normal operator launch. No Atlas process
is launched by these commands.
