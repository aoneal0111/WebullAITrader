# Warrior entry evidence audit

Run against an extracted `batch_N_capture.json` from capture_profit_export:

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.capture_entry_audit PATH_TO_CAPTURE_JSON
```

This read-only audit matches filled Warrior BUY orders to the latest preceding
AUTHORIZED lifecycle transition. It compares that authorization with nearby
symbol DECISION records with the same configuration fingerprint, using local
evaluation time rather than the provider record timestamp. Post-authorization
evaluations are excluded. Missing authorization, fingerprint, or evaluation-time
evidence produces an explicit incomplete result rather than an entry rejection.

Matching confirmation age is the age of the latest captured TRIGGERED decision
with the same setup type and numerically equal trigger. It does not establish the
exact cache lineage: nearby DECISION records need not carry the order lifecycle.
The current-confirmation comparison does not validate stop geometry, liquidity,
session, risk, or whether a submitted order filled at an executable price.
Input is limited to 8 MB, 500 orders, and 1,000 context records. Malformed inputs
fail rather than being silently interpreted as evidence.

## October 9 retrospective check

Applied to seven exported closed Warrior trades from one run:

| Entry | Current detector | Latest matching confirmation age |
| --- | --- | --- |
| CCI | TRIGGERED | 0 s |
| FSLY first | FORMING | 58.023417 s |
| IBRX | TRIGGERED | 0 s |
| NAUT first | TRIGGERED | 0 s |
| JAGX | FORMING | 41.319127 s |
| NAUT second | TRIGGERED | 0 s |
| WFF | NO_SETUP | 104.372243 s |

All three older matching confirmations remain inside the 120-second continuity
window. This reproduces the entry-evidence discrepancy; shortening expiry alone
does not test fresh structural revalidation. No counterfactual profit is computed,
no runtime policy is changed, and this selected one-day sample does not establish
that requiring a current confirmation improves future profitability.
