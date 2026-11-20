# Warrior nine-trade entry/exit evidence diagnosis

The 16 October exported JSON files represent nine distinct Warrior lifecycle
identities. Seven repeated lifecycle IDs; nine reconciled lifecycles were
already included in the aggregate. Of those nine, three had positive actual
closed P&L and six negative actual closed P&L. This report does not claim those
trades represent the full strategy distribution.

## Running the joined diagnosis

Run from an isolated checkout of the diagnosis commit, using the already
present, explicit set of nine nonoverlapping `*_capture.json` files:

```powershell
& $Python -m app.trade_intelligence.knowledge.capture_entry_exit_diagnosis @Inputs |
    Set-Content -LiteralPath $DiagnosisPath -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Entry/exit diagnosis failed" }
```

Only the input capture files are read. The implementation does not access
SQLite, the broker, network, runtime or executable trading settings.
Maximum 20 files; each 8 MB or less; combined 20 MB or less. Duplicate
campaign/lifecycle identities are rejected.

The output includes a chronological record of:

- **Actual ledger outcome:** reconciled closed P&L, actual commissions and
  initial per-trade risk (not hypothetical gains).
- **Entry provenance:** strict matching of the *same lifecycle*, campaign,
  authorization success and configuration fingerprint. Nearby symbol/time
  decisions alone do not prove the exact cached decision consumed at entry.
- **Candidate giveback evidence:** sampled 0.40R activation, first 50%
  peak-profit-retention signal time/bid, quote exclusions and missing interval
  flags. The signal is not a booked order or an achieved exit price.
- **Evidence gaps:** no matching authorization, no eligible current decision,
  or a latest sampled decision that does not agree with the authorization.

`MOST_RECENT_DECISION_MISMATCH` is an **investigation lead**, not a claim
that the strategy entered on a wrong setup. Missing provenance does not
mean missing authorization in real runtime. A later decision is not
retroactively used to justify an earlier entry. Unreconciled lifecycles do
not contribute to win/loss/P&L summaries.

## Interpretation and next engineering gate

Review each of AEHL, CCI, FSLY, IBRX, JAGX, LQDA, both NAUT lifecycles,
and WFF with the original capture and the generated per-trade evidence.
Prioritize actual mismatches and stale/future-quote evidence over guesses.
Compare winning and losing trade setup state, confirmation age, risk
geometry, and whether the exit signal existed **before** the actual loss.

Only then propose narrowly scoped entry or exit code corrections with
tests for authorization consistency, durable paper order management,
restart recovery, and controlled forward PAPER validation. Do not equate a
sampled bid with a fill or cherry-pick a decision threshold from these
nine trades.

**No runtime trading policy is changed by this analysis.**

## Choosing the strongest duplicate export

The November review identified 16 capture files but only nine distinct
lifecycles. Early exports of CCI, FSLY, IBRX, JAGX and NAUT lacked
`entry_context`; later duplicate exports each contain an exact
matching authorized `STATE_TRANSITION`. Selecting the first file
alphabetically produced an artificial 5/9 authorization-evidence gap.

The new `capture_copy_selection` module ranks candidate files by
strictly matching, successful authorization events and bounded context
completeness, then chooses a non-overlapping file set. It rejects
different ledgers or execution-price histories for the same lifecycle
instead of mixing conflicting copies. This is a deliberately
conservative check; if an old export has incomplete price-path data,
inspect the conflict before choosing one version.

Use explicit JSON filenames, not a ZIP or manifest, and do not delete
old exports. In PowerShell:

```powershell
$Copies = @(Get-ChildItem -LiteralPath $CaptureRoot -Filter "*_capture.json" -File -Recurse |
    Sort-Object FullName | Select-Object -ExpandProperty FullName)
& $Python -m app.trade_intelligence.knowledge.capture_copy_selection @Copies |
    Set-Content -LiteralPath $SelectionPath -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Capture-copy selection failed: inspect conflicts" }
$Selection = Get-Content -LiteralPath $SelectionPath -Raw | ConvertFrom-Json
& $Python -m app.trade_intelligence.knowledge.capture_entry_exit_diagnosis @($Selection.selected_files) |
    Set-Content -LiteralPath $DiagnosisPath -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Entry/exit diagnosis failed" }
```

Research correction is **not** evidence that the historical trading
gateway was missing authorization. Avoid changing live/PAPER entry
requirements from an artifact-selection error.

## November 20 conflict classification

The read-only conflict audit found exactly one duplicate discrepancy:
CCI (three copies) has identical order ledgers but different captured
execution-price-path digests. The updated copy selector can accept
strictly chronological **exact-subsequence** exports of one larger
observed path. Shared observations (including timestamp, payload,
order and repeated observations) must agree byte-for-byte after stable
JSON canonicalization. If different prices, timestamps, orderings, or
duplicate counts contradict one another, selection still fails closed.
The original copies are never modified.

An `--inspect-conflicts` report records `exact_subset_compatible`;
it still sets `selection_permitted=false` for any different digest,
even when the normal selector can safely accept a verified subset.
That conservative conflict-report flag indicates differing exports,
not a broker error. A successful complete-copy selection is the
authoritative readiness check for the subsequent offline diagnosis.

Never use `--inspect-conflicts` output as input to the diagnosis.
