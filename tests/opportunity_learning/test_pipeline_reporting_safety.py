from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
import ast

import pytest

from app.opportunity_learning.artifacts import ResearchModelArtifact, publish_artifact, verify_artifact
from app.opportunity_learning.contracts import LABEL_VERSION, LearningGeneration
from app.opportunity_learning.pipeline import OfflineLearningPipeline
from app.opportunity_learning.snapshot import ImmutableSnapshotReader, create_external_snapshot, file_identity
from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import FEATURE_VERSION, ResearchGeneration, SCHEMA_VERSION
from tests.trade_intelligence.conftest import make_experience


def generation():
    base = ResearchGeneration("G2A", "ATLAS_TEMPORAL_SPLIT_V1",
        date(2026, 6, 1), date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 31),
        date(2026, 8, 1), date(2026, 8, 31), date(2026, 8, 31), FEATURE_VERSION,
        SCHEMA_VERSION, datetime(2026, 9, 1, tzinfo=UTC))
    return LearningGeneration(base, LABEL_VERSION, "BASELINES_V1", (("regularization", "1.0"),),
                              ("validation_brier", "validation_log_loss"),
                              ("holdout_brier", "calibration", "concentration"))


def test_pipeline_reports_all_required_research_sections_and_never_promotes():
    result = OfflineLearningPipeline().run(generation(), (make_experience(),), ())
    assert result.maximum_conclusion == "INSUFFICIENT_EVIDENCE"
    assert result.selected_challenger_ids == ()
    assert set(("dataset", "champion", "sufficiency", "blockers", "pullbacks_and_momentum",
                "catalyst", "spread", "rvol_float", "setup_type", "challengers")) <= result.report.keys()
    assert all(item["status"] == "INSUFFICIENT_EVIDENCE" for item in result.report["sufficiency"].values())
    assert all(
        challenger["partitions"]["HOLDOUT"]["status"] == "UNTOUCHED_UNTIL_SELECTION"
        for challenger in result.report["challengers"].values()
    )
    assert "RESEARCH ONLY" in result.report["disclaimer"]


def test_model_card_is_small_hashed_and_immutable(tmp_path):
    artifact = ResearchModelArtifact("m1", "g1", "LOGISTIC_V1", "f1", "l1",
        ("a", "b"), ("c", "d"), ("e", "f"), (("lambda", "1"),),
        (("n", 1),), (("n", 1),), (("n", 1),), ("x",), (("method", "platt"),),
        datetime(2026, 9, 2, tzinfo=UTC))
    path = publish_artifact(artifact, tmp_path / "models")
    assert verify_artifact(path) and path.stat().st_size < 10_000
    with pytest.raises(FileExistsError):
        publish_artifact(artifact, tmp_path / "models")


def test_external_snapshot_copy_preserves_source_and_reader_is_read_only(tmp_path):
    source = tmp_path / "source.sqlite3"
    store = ExperienceStore(source)
    exp = make_experience()
    store.put_experience(exp)
    before = file_identity(source)
    snapshot = create_external_snapshot(source, tmp_path / "external")
    assert file_identity(source) == before
    reader = ImmutableSnapshotReader(snapshot.main_copy, authoritative_paths=(source,))
    assert reader.integrity_check() == "ok"
    assert reader.experiences() == (exp,)
    assert file_identity(source) == before
    with pytest.raises(ValueError, match="authoritative"):
        ImmutableSnapshotReader(source, authoritative_paths=(source,))


def test_learning_package_has_no_execution_or_runtime_dependencies():
    root = Path("app/opportunity_learning")
    forbidden_modules = ("broker", "orders", "account", "composition", "runtime")
    forbidden_calls = {"place_order", "submit_order", "authorize_order", "veto_order", "resize_order"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
        assert not any(any(part in module.lower() for part in forbidden_modules) for module in imports)
        assert forbidden_calls.isdisjoint(calls)
