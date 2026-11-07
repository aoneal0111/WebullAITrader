# Reconcile historical corpus counts before episode replay

The two operator manifests reported 3 / 3,926 accepted episodes and 7 / 8,532
strategy memberships. The historical report instead records 719,957 distinct
episode IDs and 1,582,847 strategy memberships. File size alone cannot identify
the correct corpus. Neither manifest is silently trusted or overwritten.

`tools/reconcile-equity-corpus.ps1` uses an isolated research checkout. It reads
both exact `corpus/episodes.jsonl` and `corpus_repaired/episodes.jsonl` paths in
the known June–August tranche, computes a SHA-256 while streaming, counts exact
unique IDs in a disposable SQLite database, and compares all 23 strategy counts
with the actual report. It also records date coverage and presence of referenced
normalized minute partitions. Partition presence is not price validation.

The scan is limited to 40 billion bytes per corpus, two million rows per corpus,
and one million bytes per row. Capped, malformed, changed or duplicate-ID inputs
cannot receive a matching status. The source file is opened read-only; only a
new output directory and temporary ID database are written. No installed
application database is opened. A scan of the 22.5 GB file can take several
minutes, with progress approximately every ten seconds of valid rows. There is
no resume protocol: interrupted scans are not silently treated as complete.
Completed corpus reports and sanitized failure information are retained.

Matching counts receive `COUNT_RECONCILED_NOT_CRYPTOGRAPHIC_PROVENANCE`: the
historical report does not provide a source-file digest, so a new digest does
not prove that these exact bytes generated it. Multiple matching candidates are
reported, never automatically selected. Stale manifests remain unchanged.

In that same pass the reader inventories stored `r_targets.first_plan_event`
labels for 0.5R, 1R and 2R. It keeps TARGET_FIRST, STOP_FIRST, intrabar ambiguity,
and other/missing outcomes separate. Both all-observation and resolved-only
fractions are explicit. These labels have not been recomputed from price bars;
resolved-only fractions exclude missing/ambiguous outcomes and are not net
trade win rates. The full-period labels do not become an untouched test set.
There is no cost, execution, overlap or shared-capital model in this inventory.

The next chronological price replay can reuse the existing
`app/trade_intelligence/knowledge/exit_replay.py` mechanics after corpus count
reconciliation and a frozen source digest/price coverage contract. Its current
hard-coded repaired-corpus path is not evidence that the file matches this
report. No bot, Grok request, market-data request, policy promotion, manifest
repair or trading-permission change occurs here. Attach only
`corpus-reconciliation-summary.json`, never the multi-gigabyte corpus.
