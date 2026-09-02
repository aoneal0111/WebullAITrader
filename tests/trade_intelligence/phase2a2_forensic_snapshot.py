"""Read-only forensic census for an external Phase 2A.2 snapshot copy."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from app.opportunity_discovery import default_registry
from app.trade_intelligence.discovery_runtime import discovery_observation_from_dict


TABLES = (
    "experiences", "experience_decisions", "outcomes",
    "paper_execution_observations", "work_ledger",
    "discovery_opportunity_observations", "strategy_membership_observations",
    "strategy_transition_observations", "position_correlation_observations",
    "position_thesis_observations", "add_on_research_candidates",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    path = args.snapshot.resolve(strict=True)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        result = {
            "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "counts": {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in TABLES
            },
            "dataset": {
                "experience_dates": int(connection.execute(
                    "SELECT COUNT(DISTINCT session_date) FROM experiences"
                ).fetchone()[0]),
                "experience_symbols": int(connection.execute(
                    "SELECT COUNT(DISTINCT symbol) FROM experiences"
                ).fetchone()[0]),
                "experience_sessions": int(connection.execute(
                    "SELECT COUNT(DISTINCT session) FROM experiences"
                ).fetchone()[0]),
                "unique_opportunities": int(connection.execute(
                    "SELECT COUNT(DISTINCT opportunity_id) FROM discovery_opportunity_observations"
                ).fetchone()[0]),
                "unique_opportunity_strategies": int(connection.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT opportunity_id,strategy_id "
                    "FROM strategy_membership_observations)"
                ).fetchone()[0]),
            },
            "outcomes_by_horizon_status": [tuple(row) for row in connection.execute(
                "SELECT horizon_minutes,status,COUNT(*) FROM outcomes "
                "GROUP BY horizon_minutes,status ORDER BY horizon_minutes,status"
            )],
            "ledger": [tuple(row) for row in connection.execute(
                "SELECT work_type,state,COUNT(*) FROM work_ledger "
                "GROUP BY work_type,state ORDER BY work_type,state"
            )],
            "errors": [],
            "opportunity_duplicates": int(connection.execute(
                "SELECT COUNT(*) FROM (SELECT observation_id,COUNT(*) n "
                "FROM discovery_opportunity_observations GROUP BY observation_id HAVING n>1)"
            ).fetchone()[0]),
            "membership_duplicates": int(connection.execute(
                "SELECT COUNT(*) FROM (SELECT observation_id,COUNT(*) n "
                "FROM strategy_membership_observations GROUP BY observation_id HAVING n>1)"
            ).fetchone()[0]),
            "membership_orphans": int(connection.execute(
                "SELECT COUNT(*) FROM strategy_membership_observations m WHERE NOT EXISTS ("
                "SELECT 1 FROM discovery_opportunity_observations o "
                "WHERE o.opportunity_id=m.opportunity_id)"
            ).fetchone()[0]),
            "future_cutoff_violations": 0,
            "strategy_memberships": [tuple(row) for row in connection.execute(
                "SELECT strategy_id,COUNT(*),COUNT(DISTINCT opportunity_id) "
                "FROM strategy_membership_observations GROUP BY strategy_id ORDER BY strategy_id"
            )],
            "overlaps": [],
        }
        error_counts: Counter[tuple[str, str, str]] = Counter()
        detector_failure_counts: Counter[tuple[str, str, str]] = Counter()
        for row in connection.execute(
            "SELECT work_type,error,payload_json FROM work_ledger WHERE state='FAILED' ORDER BY work_id"
        ):
            error_class = "UNKNOWN"
            message = str(row["error"])
            try:
                error_payload = json.loads(message)
                error_class = str(error_payload.get("error_class", error_class))
                message = str(error_payload.get("message", message))
            except (TypeError, ValueError):
                pass
            error_counts[(str(row["work_type"]), error_class, message)] += 1
            if row["work_type"] == "DISCOVERY":
                observation = discovery_observation_from_dict(json.loads(row["payload_json"]))
                for detector in default_registry().detectors:
                    try:
                        detector.detect(observation.context)
                    except Exception as exc:  # forensic classification of persisted failed input
                        detector_failure_counts[(
                            detector.definition.strategy_id,
                            type(exc).__name__,
                            str(exc),
                        )] += 1
        result["errors"] = [
            {
                "work_type": work_type,
                "error_class": error_class,
                "message": message,
                "count": count,
            }
            for (work_type, error_class, message), count in sorted(error_counts.items())
        ]
        result["detector_failures"] = [
            {
                "strategy_id": strategy_id,
                "error_class": error_class,
                "message": message,
                "count": count,
            }
            for (strategy_id, error_class, message), count in sorted(detector_failure_counts.items())
        ]
        overlap_counter = Counter()
        opportunity_ids = connection.execute(
            "SELECT DISTINCT opportunity_id FROM discovery_opportunity_observations"
        ).fetchall()
        for row in opportunity_ids:
            strategies = tuple(item[0] for item in connection.execute(
                "SELECT DISTINCT strategy_id FROM strategy_membership_observations "
                "WHERE opportunity_id=? ORDER BY strategy_id", (row[0],),
            ))
            overlap_counter[strategies] += 1
        result["overlaps"] = [
            {"strategies": strategies, "opportunities": count}
            for strategies, count in overlap_counter.most_common()
        ]
        for row in connection.execute(
            "SELECT payload_json,decision_cutoff FROM strategy_membership_observations"
        ):
            payload = json.loads(row[0])
            cutoff = row[1]
            # Detector evidence is decision-time metadata; timestamps embedded
            # in setup anchors/reasons must never exceed the persisted cutoff.
            for key, value in payload.items():
                if key.endswith("_time") or key.endswith("_timestamp"):
                    if isinstance(value, str) and value > cutoff:
                        result["future_cutoff_violations"] += 1
        print(json.dumps(result, sort_keys=True, default=str))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
