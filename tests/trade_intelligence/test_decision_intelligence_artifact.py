import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.trade_intelligence.decision_intelligence.artifact import (
    build_artifact,
    validate_artifact,
)
from app.trade_intelligence.decision_intelligence.models import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactValidationError,
)

REPORT = Path("data/research/historical_tranches/2026_06_01__2026_08_31/reports/full_research_final.json")


def _build(tmp_path):
    target = tmp_path / "historical_decision_intelligence.sqlite3"
    build_artifact(REPORT, target, baseline="c3550d020bd3af1e8131dbfaf70507ca16efb356")
    return target


def test_report_builds_and_validates_read_only(tmp_path):
    target = _build(tmp_path)
    result = validate_artifact(target, source_report_path=REPORT, read_only=True)
    assert result.valid
    with sqlite3.connect(f"file:{target.resolve().as_posix()}?mode=ro", uri=True) as db:
        assert db.execute("select count(*) from strategy_evidence").fetchone()[0] == 23


def test_source_hash_and_metadata_are_persisted(tmp_path):
    target = _build(tmp_path)
    with sqlite3.connect(target) as db:
        metadata = dict(db.execute("select key,value from artifact_metadata"))
    assert metadata["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert metadata["source_report_sha256"] == hashlib.sha256(REPORT.read_bytes()).hexdigest()
    assert metadata["source_episode_count"] == "719957"
    assert metadata["source_membership_count"] == "1582847"
    assert metadata["builder_commit"] == "c3550d020bd3af1e8131dbfaf70507ca16efb356"
    assert metadata["artifact_build_baseline"] == metadata["source_report_baseline"]


def test_representative_strategy_parity(tmp_path):
    target = _build(tmp_path)
    source = json.loads(REPORT.read_text(encoding="utf-8"))
    with sqlite3.connect(target) as db:
        for name in ("PREMARKET_CONSOLIDATION_BREAKOUT", "PREMARKET_HIGH_BREAKOUT", "FIRST_PULLBACK", "HIGH_OF_DAY_BREAKOUT", "HOD_RECLAIM", "FLAT_TOP_BREAKOUT"):
            row = db.execute("select whole_sample_count,median_mfe,median_mae,median_max_r,descriptive_tier from strategy_evidence where strategy=?", (name,)).fetchone()
            assert row[0] == source["strategy_scorecards"][name]["sample_count"]
            assert row[1:4] == tuple(source["strategy_scorecards"][name][k] for k in ("median_mfe", "median_mae", "median_maximum_r"))


def test_transition_parity(tmp_path):
    target = _build(tmp_path)
    source = next(x for x in json.loads(REPORT.read_text(encoding="utf-8"))["reentry"]["transition_matrix"] if x["parent_strategy"] == "FIRST_PULLBACK" and x["child_strategy"] == "CONSOLIDATION_BREAKOUT")
    with sqlite3.connect(target) as db:
        row = db.execute("select sample_count,median_mfe,median_mae,median_max_r from transition_evidence where parent_strategy=? and child_strategy=?", (source["parent_strategy"], source["child_strategy"])).fetchone()
    assert row == (source["sample_count"], source["median_mfe"], source["median_mae"], source["median_maximum_r"])


def test_missing_required_section_fails_closed(tmp_path):
    report = json.loads(REPORT.read_text(encoding="utf-8")); report.pop("walk_forward")
    path = tmp_path / "bad.json"; path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        build_artifact(path, tmp_path / "artifact.sqlite3")


def test_source_hash_change_is_detected(tmp_path):
    target = _build(tmp_path)
    changed = tmp_path / "changed.json"; changed.write_bytes(REPORT.read_bytes() + b" ")
    result = validate_artifact(target, source_report_path=changed, read_only=True)
    assert not result.valid and any("SHA-256" in error for error in result.errors)


def test_atomic_failure_preserves_existing_artifact(tmp_path):
    target = _build(tmp_path); before = target.read_bytes()
    bad = tmp_path / "bad.json"; bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        build_artifact(bad, target)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_limitation_rows_and_component_versions_exist(tmp_path):
    target = _build(tmp_path)
    with sqlite3.connect(target) as db:
        codes = {x[0] for x in db.execute("select code from limitations")}
        metadata = dict(db.execute("select key,value from artifact_metadata"))
    assert {"NO_NBBO", "NO_SPREAD", "NO_LEVEL2", "NO_HISTORICAL_CATALYST_NEWS", "BAR_VOLUME_CAPACITY_PROXY_ONLY"} <= codes
    assert "ATLAS_BENCHMARK_REGIME_V1" in metadata["research_component_versions"]


def test_shared_payloads_are_stored_once(tmp_path):
    target = _build(tmp_path)
    with sqlite3.connect(target) as db:
        assert db.execute("select count(*) from global_evidence").fetchone()[0] == 5
        rows = db.execute("select execution_robustness_json,market_regime_robustness_json from strategy_evidence").fetchall()
    assert all(row == (None, None) for row in rows)
    assert target.stat().st_size < 10_000_000


def test_rebuilds_have_equivalent_logical_rows(tmp_path):
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "two")
    tables = ("strategy_evidence", "context_evidence", "progression_evidence", "transition_evidence", "failure_evidence", "limitations")
    with sqlite3.connect(first) as left, sqlite3.connect(second) as right:
        for table in tables:
            assert left.execute(f"select count(*) from {table}").fetchone() == right.execute(f"select count(*) from {table}").fetchone()


def test_duplicate_strategy_key_is_rejected_by_sqlite(tmp_path):
    target = _build(tmp_path)
    with sqlite3.connect(target) as db:
        row = db.execute("select * from strategy_evidence limit 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into strategy_evidence select * from strategy_evidence limit 1")


def test_di2_service_is_present_but_does_not_expose_runtime_policy():
    package = Path("app/trade_intelligence/decision_intelligence")
    assert (package / "service.py").exists()
    source = (package / "service.py").read_text(encoding="utf-8")
    assert "class HistoricalDecisionIntelligence" in source
    assert "paper_entry_submitter" not in source
    assert "place_order" not in source
