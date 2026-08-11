"""Sanitized audit summary for a captured Warrior forward database."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path

from .forward_models import CaptureRecordType
from .forward_replay import replay_captured_decision
from .forward_store import ForwardCaptureStore


def audit(path: Path) -> dict[str, object]:
    store = ForwardCaptureStore(path)
    records = store.records()
    discoveries = [item for item in records if item.record_type is CaptureRecordType.DISCOVERY]
    decisions = [item for item in records if item.record_type is CaptureRecordType.DECISION]
    transitions = [item for item in records if item.record_type is CaptureRecordType.STATE_TRANSITION]
    qualities = [item for item in records if item.record_type is CaptureRecordType.DATA_QUALITY]
    catalysts = [item for item in records if item.record_type is CaptureRecordType.CATALYST_EVIDENCE]
    spreads = [item for item in records if item.record_type is CaptureRecordType.SPREAD_EVIDENCE]
    equivalence = [replay_captured_decision(store, item.record_id) for item in decisions]
    top = sorted(discoveries, key=lambda item: Decimal(item.payload["momentum_score"]), reverse=True)[:10]
    blocked: Counter[str] = Counter()
    for item in transitions:
        if item.payload.get("to") == "ENTRY_BLOCKED":
            blocked.update(gate["gate"] for gate in item.payload.get("blocking_gates", ()))
    missing: Counter[str] = Counter()
    for item in qualities:
        missing.update(key for key, value in item.payload.items() if value)
    catalyst_states = Counter(item.payload["evidence_state"] for item in catalysts)
    triggered_details = []
    for decision in decisions:
        setup = decision.payload.get("setup")
        if not setup or setup.get("state") != "TRIGGERED":
            continue
        matching = [
            item.payload for item in transitions
            if item.symbol == decision.symbol and item.timestamp == decision.timestamp
            and item.payload.get("to") == "ENTRY_BLOCKED"
        ]
        triggered_details.append({
            "symbol": decision.symbol, "setup": setup["type"],
            "score": decision.payload["score"],
            "reason_codes": decision.payload["reason_codes"],
            "blocking_gates": (
                [] if not matching else matching[-1].get("blocking_gates", [])
            ),
        })
    catalyst_example = next(
        (item for item in catalysts if item.payload["evidence_state"] == "TRUE"),
        catalysts[0] if catalysts else None,
    )
    return {
        "schema_version": records[0].schema_version if records else 1,
        "integrity_check": store.integrity_check(),
        "record_count": len(records),
        "discovered": len(discoveries),
        "stocks_in_play": sum(bool(item.payload["stocks_in_play"]) for item in discoveries),
        "triggered_setups": sum(item.payload.get("to") == "SETUP_TRIGGERED" for item in transitions),
        "entry_ready": sum(item.payload.get("to") == "ENTRY_READY" for item in transitions),
        "paper_entries": sum(item.payload.get("action") == "ENTRY" for item in records if item.record_type is CaptureRecordType.PAPER_FILL),
        "counterfactual_starts": sum(
            item.payload.get("action") == "START" for item in records
            if item.record_type is CaptureRecordType.COUNTERFACTUAL
        ),
        "authoritative_spreads": sum(bool(item.payload["authoritative"]) for item in spreads),
        "catalyst_states": dict(sorted(catalyst_states.items())),
        "missing_data_counts": dict(sorted(missing.items())),
        "blocking_gates": dict(sorted(blocked.items())),
        "triggered_details": triggered_details,
        "replay_decisions": len(equivalence),
        "replay_equivalent": sum(item.equivalent for item in equivalence),
        "top_momentum": [
            {
                "symbol": item.symbol,
                "score": item.payload["momentum_score"],
                "stocks_in_play": item.payload["stocks_in_play"],
                "spread_percent": item.payload["spread_percent"],
                "catalyst_state": item.payload["catalyst_state"],
            }
            for item in top
        ],
        "sanitized_examples": {
            "discovery": None if not discoveries else discoveries[0].payload,
            "catalyst": None if catalyst_example is None else catalyst_example.payload,
            "spread": None if not spreads else spreads[0].payload,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path", type=Path,
        default=Path("data/warrior_momentum_v1_forward/forward_capture.sqlite3"),
    )
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.path), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
