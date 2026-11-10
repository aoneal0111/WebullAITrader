# Entry and stop evidence review

`capture-equity-entry-evidence.ps1` reads an existing interrupted PAPER review and
the original journal still on the workstation. No Atlas restart, provider request,
model request, database mutation, or parameter change occurs.

The exporter verifies the original manifest, every checkpoint-prefix record hash,
sequence and baseline, each segment hash, and aggregate byte count and chain head.
It excludes all bytes after the reviewed checkpoint. The private compact archive
contains all recorded native assessments, confirmation diagnostics, PAPER events,
and readiness failures, plus stream assessments and market events received from
60 seconds before each filled trade through 60 seconds after its close.

All-prefix stream reason counts distinguish negative provider age from nonnegative
age for LAST/BID/ASK. Negative age can indicate clock skew; its frequency does not
justify changing clocks, treating old data as fresh, or changing execution policy.
A nonnegative age alone is not a complete freshness approval.

Selected data is bounded to 25,000 records / 24 MiB uncompressed and an 8 MiB ZIP.
If the bound is exceeded, export refuses and removes its partial file rather than
silently cutting coverage. Original history stays unchanged. The selected sample
supports causal debugging and later parameter comparisons, not independent proof
of profitability. Wider stops and extra buy signals do not establish improvement.
