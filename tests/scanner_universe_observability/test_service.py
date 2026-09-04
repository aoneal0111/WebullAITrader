from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from app.scanner_universe_observability import (
    ScannerUniverseAdmissionObserver,
    UniverseAdmissionOutcome,
    UniverseAdmissionStage,
)


NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)


def test_append_only_jsonl_is_research_only_and_duplicate_cardinality_is_bounded(tmp_path):
    path = tmp_path / "universe.jsonl"
    observer = ScannerUniverseAdmissionObserver(enabled=True, path=path, capacity=16)
    observer.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    for _ in range(10_000):
        observer.record(
            stage=UniverseAdmissionStage.SCANNER_EVALUATION_REACHED,
            outcome=UniverseAdmissionOutcome.REACHED,
            reason="ACTIVE_SYMBOL_EVENT_ENTERED_SCANNER_PIPELINE",
            raw_symbol="IMRN",
            normalized_symbol="IMRN",
        )
    assert observer.close()

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 2  # refresh boundary plus one scanner boundary
    assert records[-1]["research_only"] is True
    assert records[-1]["selection_authorized"] is False
    assert records[-1]["execution_authorized"] is False
    metrics = observer.metrics()
    assert metrics.accepted == 2
    assert metrics.suppressed == 9_999
    assert metrics.completed == 2
    assert metrics.queue_high_water <= 2


def test_new_refresh_has_new_identity_and_remains_bounded(tmp_path):
    observer = ScannerUniverseAdmissionObserver(
        enabled=True, path=tmp_path / "refreshes.jsonl", capacity=16
    )
    for minute in range(3):
        observer.begin_refresh(
            timestamp=NOW.replace(minute=minute), session="REGULAR", page_size=50
        )
        for _ in range(100):
            observer.record(
                stage=UniverseAdmissionStage.SCREENER_RETURNED,
                outcome=UniverseAdmissionOutcome.OBSERVED,
                reason="UPSTREAM_RESPONSE_ROW",
                screener_identity="DAY_GAINERS",
                source_rank=1,
                raw_symbol="IMRN",
            )
    observer.close()
    assert observer.metrics().accepted == 6
    assert observer.metrics().suppressed == 297
    assert observer.metrics().refresh_count == 3


class _FailingStore:
    def append(self, event):
        raise OSError("disk unavailable")

    def close(self):
        pass


def test_persistence_failure_is_contained_and_worker_drains(tmp_path):
    observer = ScannerUniverseAdmissionObserver(
        enabled=True,
        path=tmp_path / "failed.jsonl",
        store_factory=lambda _: _FailingStore(),
    )
    observer.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    observer.record(
        stage=UniverseAdmissionStage.UNIVERSE_ADMITTED,
        outcome=UniverseAdmissionOutcome.ACCEPTED,
        reason="REFERENCE_WARMUP_SUCCEEDED",
        normalized_symbol="IMRN",
    )
    assert observer.close()
    metrics = observer.metrics()
    assert metrics.failed == 2
    assert metrics.outstanding == 0
    assert metrics.last_error_type == "OSError"


def test_disabled_observer_creates_no_artifact(tmp_path):
    path = tmp_path / "disabled.jsonl"
    observer = ScannerUniverseAdmissionObserver(enabled=False, path=path)
    observer.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    observer.record(
        stage=UniverseAdmissionStage.UNIVERSE_ADMITTED,
        outcome=UniverseAdmissionOutcome.ACCEPTED,
        reason="REFERENCE_WARMUP_SUCCEEDED",
        normalized_symbol="IMRN",
    )
    assert observer.close()
    assert not path.exists()


def test_package_has_no_selection_or_execution_authority_imports():
    package = Path(__file__).parents[2] / "app" / "scanner_universe_observability"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = (
        "place_order", "submit_order", "cancel_order", "replace_order",
        "authorize_order", "rank_candidates", "is_eligible(", "paper_gateway",
        "live_execution", "risk_override",
    )
    assert all(name not in text for name in forbidden)
