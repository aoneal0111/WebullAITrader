# Warrior decision-lineage evidence (read-only)

This follow-up to PR #118 adds an exact-source comparison to the existing
offline entry and entry/exit audits. It does not change trading permissions,
order gateways, signal generation, timeouts, or broker behavior.

For a filled Warrior BUY, the audit first identifies the matching authorized
transition by lifecycle, campaign, symbol, and pre-order time. It then considers
DECISION records that were evaluated no later than authorization time and match
the authorization configuration fingerprint. If a valid 24-character
`source_decision_id` identifies exactly one eligible DECISION, the audit now
reports its state, setup type, whether its TRIGGERED setup/type/numeric trigger
agree with the authorized setup, and its age at authorization.

The existing **latest nearby decision** fields remain separate: a later
FORMING or NO_SETUP assessment may disagree with the authorized candidate
without establishing that the authorization was incorrect. Matching source IDs
prove record correlation under capture constraints, **not** the snapshot
consumed at the gateway. Missing IDs, missing source DECISION records and
duplicate eligible IDs remain explicitly unknown/ambiguous. The audit never
fabricates an implied decision or hypothetical fill.

New optional fields in `audit_entries` and each `entry_details` row of
`diagnose_captures`:
- `source_decision_state`
- `source_decision_setup`
- `source_decision_matches_authorization` (true / false / null)
- `source_decision_age_seconds` (decimal string / null)

`source_decision_is_gateway_snapshot_proof` remains false.

To validate on Windows using the existing isolated worktree, run:

```powershell
$Repo = "$env:USERPROFILE\AtlasDecisionResearch-20261120"
$Python = "$env:USERPROFILE\WebullAITrader\.venv\Scripts\python.exe"
Set-Location $Repo
& $Python -m pytest -q tests/trade_intelligence/test_capture_entry_audit.py tests/trade_intelligence/test_capture_entry_exit_diagnosis.py tests/warrior_momentum/test_source_decision_provenance.py
```

To test this pull request, use a worktree checked out at its proposed branch,
not the old research worktree. No historical October capture can acquire the
new source decision ID retroactively. Observe a new bounded PAPER capture and
review exact-source comparison before proposing any runtime admission change.
