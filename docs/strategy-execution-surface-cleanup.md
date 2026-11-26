# Atlas active strategy surface — cleanup evidence

Baseline: `research/high-win-rate-comparison` at `12502fda`.
This is a **static code map**, not a broker permission or profitability audit.

| Surface | Verified source | Interpretation |
| --- | --- | --- |
| Warrior Momentum PAPER bridge | `app/composition/desktop.py` imports `AutonomousPaperExecutionBridge` | Desktop execution path; retain |
| Quick Scalper PAPER runtime | `app/composition/desktop.py` imports `QuickScalperPaperRuntimeAdapter` | Desktop execution path; retain |
| Crypto research | `app/composition/desktop.py` and `desktop_optional_research.py` import `app.crypto_research` | Optional research runtimes, not independently proof of order authority |
| Equity replacement policies | `app/equity_overhaul/admission.py` | `RELEASE_PROMOTIONS` is empty: no replacement strategy promoted |
| Crypto discovery definitions | `app/crypto_research/strategies.py` | Defaults require research-only and prohibit selection/execution authorization |
| External Freqtrade experiments | `app/strategy_lab/FleetWave.py`, `AtlasBollingerResearch.py`, `runtime_check.py` | Independent experiment/validation entrypoints; zero static imports is **not** safe-delete evidence |

Run the compositional static map offline:

```powershell
$Repo = "$env:USERPROFILE\WebullAITrader"
& "$Repo\.venv\Scripts\python.exe" "$Repo\tools\strategy_execution_surface_inventory.py" --repo $Repo
```

Next removal review: for every candidate check CLI launch commands, Freqtrade
strategy loader, dynamic import, configurations, docs, tests, replay archives,
and source locks. Then remove **only proven redundant code** with focused
tests, full CI and rollback. Avoid speculative code deletion from current
runtime, crypto research, or historical forensic modules.

The map may detect import/gate drift and refuse to classify without all
sources. It does not execute orders, load the desktop, touch an account,
delete files, migrate PAPER campaigns, or authorize strategy promotions.
