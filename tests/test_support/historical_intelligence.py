"""Synthetic evidence for contract tests; never a production research source."""
import json
from pathlib import Path

from app.opportunity_discovery import default_registry, DetectorAvailability
from app.trade_intelligence.decision_intelligence.artifact import build_artifact
from app.trade_intelligence.decision_intelligence.models import REQUIRED_SECTIONS


def write_report(path: Path) -> Path:
    strategies = [
        item.definition.strategy_id for item in default_registry().detectors
        if item.definition.availability is DetectorAvailability.ACTIVE
    ]
    # The V1 builder requires these corpus-count constants even in schema fixtures.
    # They are test metadata, not measured research results.
    report = {section: {} for section in REQUIRED_SECTIONS}
    report.update({
        "metadata": {"episode_count": 719957, "membership_count": 1582847,
                     "source": "SYNTHETIC_TEST_FIXTURE"},
        "strategy_scorecards": {
            name: {"sample_count": 100, "median_mfe": 3.0,
                   "median_mae": -1.0, "median_maximum_r": 2.0,
                   "confidence_state": "MODERATE_EVIDENCE"}
            for name in strategies
        },
        "reentry": {"transition_matrix": [{
            "parent_strategy": "FIRST_PULLBACK",
            "child_strategy": "CONSOLIDATION_BREAKOUT",
            "sample_count": 25, "median_mfe": 2.5,
            "median_mae": -0.5, "median_maximum_r": 1.5,
        }]},
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def write_artifact(directory: Path) -> Path:
    report = write_report(directory / "synthetic-report.json")
    artifact = directory / "synthetic-intelligence.sqlite3"
    build_artifact(report, artifact, baseline="synthetic-test-fixture")
    return artifact
