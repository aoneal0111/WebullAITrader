# Warrior authorization timing: four-trade comparison

Read-only analysis of four reconciled October Warrior entries selected from
the complete nine-trade diagnosis: FSLY, JAGX and WFF (losses), and AEHL
(a winner). The earlier audit found matching authorizations for all nine,
but the four highlighted entries had latest nearby DECISION mismatches.

The capture's latest pre-authorization DECISION is **not** a proven
identity-matched cache snapshot delivered to the execution gateway.
A mismatch is a research lead, **not** a demonstrated defective entry.
The old WFF matching confirmation age was 104.372243 seconds and AEHL's
7.44189 seconds. These observed ages are not sufficient to derive a
universal maximum confirmation age, especially where fresh catalyst-backed
continuation is desirable.

Run in an isolated checkout after the complete-copy selector and nine-trade
diagnosis succeed. Keep the original archived JSON files untouched:

```powershell
& $Python -m app.trade_intelligence.knowledge.capture_authorization_timing $DiagnosisPath |
    Set-Content -LiteralPath $TimingPath -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Authorization timing comparison failed" }
$Timing = Get-Content -LiteralPath $TimingPath -Raw | ConvertFrom-Json
$Timing.summary | Format-List
$Timing.trades | Select-Object symbol,actual_closed_pnl,
    latest_decision_state,matching_confirmation_age_seconds,
    authorization_to_order_ms,eligible_quotes |
    Format-Table -AutoSize
```

All timestamps are offset-aware. A missing matching confirmation is reported
as null, never as age zero or evidence of staleness. The tool rejects
unreconciled outcomes, missing entries, missing targets, invalid times,
nonfinite ages, ambiguous multiple buy entries and policy-scope ambiguity.
It reads one existing diagnosis file (up to 4 MB) and does not access
broker state, SQLite, network, or runtime policy.

## Next validation gate

Compare the **actual authorized decision snapshot** used by the gateway
to the decision cache at the same lifecycle, configuration fingerprint,
campaign and authorization instant. This requires evidence not guaranteed
by the nearby-context export. A future observability change may add an
explicit immutable snapshot ID to the authorization transition and
matching decision event. Such work needs bounded data, safe defaults,
privacy controls and no effects on trading behavior.

Only once authorization lineage is proven should one consider changing
confirmation freshness or continuation-entry policy, and only after both
historical replay and controlled forward PAPER validation. Do not remove
catalyst-driven continuation or chase handling based on a nine-trade sample.
