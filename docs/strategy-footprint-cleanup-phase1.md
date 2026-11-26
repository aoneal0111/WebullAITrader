# Atlas strategy footprint cleanup — Phase 1

This phase inventories potential redundancy before deleting code. The research
branch is `research/high-win-rate-comparison`; the active PAPER campaign and
the September legacy campaign must remain unchanged.

## Verified repository observations (commit bc6e4a99)

- 3,017 tracked files; 1,435 under `app/`, 1,227 under `tests/`,
  229 under `docs/`, and 99 under `tools/`.
- No tracked `__pycache__`, `.pyc`, `.sqlite`, `.db`, `.log`, or `.zip`
  files were found in a complete recursive repository tree listing.
- Strategy implementations and research are spread across `app/strategy`,
  `app/strategy_engine`, `app/strategies`, `app/strategy_lab`,
  `app/strategy_research`, `app/equity_overhaul`, and
  `app/crypto_research`. These are not interchangeable.
- `app/strategy_engine` is imported by the execution coordinator;
  `app/strategy/momentum.py` is imported by `app/strategy/scoring.py`.
  Neither can be deleted merely because Warrior Momentum has other code.

## Run an offline candidate report

```powershell
$Repo = "$env:USERPROFILE\WebullAITrader"
& "$Repo\.venv\Scripts\python.exe" "$Repo\tools\strategy_footprint_audit.py" --repo $Repo --output "$env:TEMP\atlas-strategy-footprint.json"
```

The script parses imports in `app/`, `tests/`, and `tools/`, and counts
static consumers of strategy modules. It only reads project source and
writes the requested JSON report. A module with zero static consumers is
**not** proof it can be deleted. Dynamic module loading, subprocess entry
points, CLI commands, runtime configuration strings, documentation references,
test monkeypatches, and executable scripts still need review.

## Removal gate for each candidate

1. Identify any runtime, test, research, migration, diagnostics, or entry-point
   consumers beyond static imports.
2. Determine whether it is a production strategy or an offline research tool.
3. Preserve frozen datasets, replay evidence, legacy campaign information,
   and regression tests even when their runtime entry paths are disabled.
4. Remove only a bounded, verified unused or duplicate path in its own PR.
5. Run focused tests, full CI, and a clean worktree review before merging.
6. Never treat deleting strategy code as evidence of improved trading win rate.

**No strategy deletions are included in this Phase 1 audit.**


## Phase 2: inspect standalone consumers before deleting anything

The V2 audit distinguishes `if __name__ == "__main__"` CLI entrypoints,
Freqtrade `IStrategy` subclasses, and Python string-literal references
from normal imports. These signals can help explain why an apparently unused
module is actually run by a CLI or an external loader. Literal matches are
heuristic, not authoritative. Absence of all three signals is still **not**
deletion proof: shell scripts, configuration files, external user-data
directories and historical frozen source locks can invoke modules by name.

Do not delete any of the original 20 candidates solely on this report.
