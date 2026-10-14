"""Read-only performance breakdown of saved observational learning outcomes."""
import argparse
import json
from pathlib import Path

from app.asset_modules.learning_outcomes import outcome_summary
from app.asset_modules.contract_learning_outcomes import proxy_summary
from app.trade_intelligence.knowledge.capture_profit_export import read_database, bounded_rows


def read_performance(path):
    with read_database(path) as db:
        db.execute("BEGIN")
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        allowed = {"learning_experiments_v1", "learning_outcomes_v1", "learning_proxy_outcomes_v1"}
        if not names or names - allowed:
            raise ValueError("Dedicated learning journal required")
        saved = {}
        for kind, table in (("equity","learning_outcomes_v1"), ("simulations","learning_proxy_outcomes_v1")):
            rows = (bounded_rows(db, "SELECT payload FROM "+table+" ORDER BY identity LIMIT 100001",
                                 (),100000,64_000_000) if table in names else [])
            saved[kind] = [json.loads(r[0]) for r in rows]
    return {"version":"LEARNING_OUTCOME_PERFORMANCE_V1",
            "scope":"DESCRIPTIVE_RECORDED_OUTCOMES_NOT_POLICY_SELECTION_OR_HOLDOUT_PROOF",
            "equity":outcome_summary(saved["equity"]),
            "simulations":proxy_summary(saved["simulations"]),
            "orders":0, "network_requests":0, "promotion":"NONE"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(read_performance(args.journal),indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
