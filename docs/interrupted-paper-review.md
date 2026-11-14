# Interrupted PAPER session review

When Atlas was stopped without producing `research-summary.json`, use
`tools/capture-paper-day-review.ps1 -Interrupted` with the existing trial and
an exact reviewed revision. This exports evidence; it does not start Atlas,
renew the trial, alter its settings, or enable orders. Running crypto containers
are independent and can remain running.

The recovery path checks actual Windows Atlas/Grok Python producers before and
after collection. An old `writer_alive` value is preserved as an observation,
not treated as current process health. A present final summary requires the
normal export path instead.

The saved checkpoint must match the manifest baseline. Its written journal
prefix is streamed and checked for record sequence, SHA-256 links, baseline,
byte count and final chain head. Changes to the checkpoint, segment sizes or
modification times, trial pointer or manifest abort export. Bytes after the
checkpoint, including partial lines, are excluded and unverified. Original
recording files are never modified or finalized.

The compact `interrupted-paper-session-review.zip` contains metadata, prefix
verification counts and hashes, and read-only SQLite backups. Raw market journals
are excluded. Trial-specific fills are reconciled with the same logic used by
the normal review. Open or ambiguous inventory is not reported as zero-profit
closed trades. The ledger backup and journal checkpoint are separate observations,
not an atomic snapshot. Missing final engine counters remain unavailable.

Tests cover source preservation, partial-tail exclusion, corrupt/missing prefixes,
active producers, changing checkpoints, and trial-bound fill reconciliation.
Windows process inspection and PowerShell integration must run on the workstation;
the standard-library export and reconciliation tests also run without Windows.
