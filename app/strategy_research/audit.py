"""Export all reconciled active-campaign equity outcomes without journal writes."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("data/sandbox/paper-execution.sqlite3"))
    args = parser.parse_args(argv)
    # Reuse the canonical read-only snapshot and lifecycle reconciliation.
    # Do not use learning_outcomes.main: it writes a separate learning journal.
    from app.asset_modules.learning_outcomes import read_equity_outcomes, outcome_summary
    try:
        report = read_equity_outcomes(args.ledger)
        summary = outcome_summary(report["outcomes"])
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Equity audit failed: {exc}\n")
    print(json.dumps({
        "version": "STRATEGY_RESEARCH_EQUITY_AUDIT_V1",
        "captured_at": datetime.now(UTC).isoformat(),
        "selection": "ALL_RECONCILED_CLOSED_ACTIVE_CAMPAIGN_EQUITY_LIFECYCLES",
        "source": report,
        "summary": summary,
        "orders": 0, "network_requests": 0, "execution_authorized": False,
        "limitations": ["Active campaign only; excluded/unresolved lifecycles are reported separately.",
                        "Recorded commissions only; source PAPER provenance is unverified.",
                        "Observational outcomes do not prove which replacement policy is better."],
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
