from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.learning import (
    AtomicModelRegistry,
    ImmutableJournalDatasetExporter,
    ModelEvaluation,
    PromotionPolicy,
    RegisteredModel,
)


NOW = datetime(2026, 7, 20, 18, 0, tzinfo=UTC)


def _journal(path):
    payload = {
        "schema_version": "1",
        "session_id": "paper-1",
        "cycles": [
            {
                "cycle": 1,
                "timestamp": NOW.isoformat(),
                "symbols": ["MSFT", "AAPL"],
                "decisions": [
                    {"symbol": "MSFT", "action": "HOLD"},
                    {"symbol": "AAPL", "action": "BUY"},
                ],
                "session_statistics": {
                    "current_equity": "10010",
                    "current_drawdown": "0",
                    "realized_pnl": "0",
                    "unrealized_pnl": "10",
                    "decisions_processed": 2,
                    "orders_attempted": 1,
                    "orders_filled": 1,
                    "orders_rejected": 0,
                },
            }
        ],
        "latest_analytics": None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _model(model_id, score="1.0", drawdown="0.10", samples=100):
    return RegisteredModel(
        model_id=model_id,
        created_at=NOW,
        dataset_id="dataset-abc",
        artifact_sha256=hashlib.sha256(model_id.encode()).hexdigest(),
        git_commit="3536465",
        feature_names=("cycle", "symbol_ordinal"),
        hyperparameters=(("depth", "3"),),
        evaluation=ModelEvaluation(
            walk_forward_score=Decimal(score),
            stress_score=Decimal("0.8"),
            max_drawdown=Decimal(drawdown),
            sample_count=samples,
        ),
    )


def _policy():
    return PromotionPolicy(
        minimum_walk_forward_score=Decimal("0.5"),
        minimum_stress_score=Decimal("0.5"),
        maximum_drawdown=Decimal("0.20"),
        minimum_sample_count=50,
        required_improvement=Decimal("0.05"),
    )


def test_dataset_export_is_deterministic_and_immutable(tmp_path):
    journal = tmp_path / "journal.json"
    output = tmp_path / "datasets"
    _journal(journal)
    exporter = ImmutableJournalDatasetExporter()

    first = exporter.export(journal_path=journal, output_directory=output, created_at=NOW)
    second = exporter.export(journal_path=journal, output_directory=output, created_at=NOW)

    assert first.dataset_id == second.dataset_id
    assert first.record_count == 2
    assert first.records_path.read_bytes() == second.records_path.read_bytes()
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["records_sha256"] == hashlib.sha256(first.records_path.read_bytes()).hexdigest()
    assert [json.loads(line)["symbol"] for line in first.records_path.read_text().splitlines()] == ["MSFT", "AAPL"]


def test_dataset_export_rejects_empty_journal(tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps({"schema_version": "1", "session_id": "x", "cycles": []}))
    with pytest.raises(ValueError, match="no training records"):
        ImmutableJournalDatasetExporter().export(journal_path=journal, output_directory=tmp_path, created_at=NOW)


def test_dataset_export_rejects_misaligned_decisions(tmp_path):
    journal = tmp_path / "journal.json"
    _journal(journal)
    payload = json.loads(journal.read_text())
    payload["cycles"][0]["decisions"].pop()
    journal.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="must align"):
        ImmutableJournalDatasetExporter().export(journal_path=journal, output_directory=tmp_path, created_at=NOW)


def test_registry_registers_and_promotes_first_model(tmp_path):
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    model = _model("model-1")
    registry.register(model)
    promoted = registry.promote(model.model_id, _policy(), NOW)
    assert promoted == model
    assert registry.active_model() == model


def test_registry_rejects_duplicate_model(tmp_path):
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    model = _model("model-1")
    registry.register(model)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(model)


def test_promotion_rejects_candidate_below_gate_without_changing_active(tmp_path):
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    incumbent = _model("incumbent", score="1.0")
    weak = _model("weak", score="0.4")
    registry.register(incumbent)
    registry.register(weak)
    registry.promote(incumbent.model_id, _policy(), NOW)
    with pytest.raises(ValueError, match="walk-forward score below minimum"):
        registry.promote(weak.model_id, _policy(), NOW)
    assert registry.active_model() == incumbent


def test_promotion_requires_improvement_over_incumbent(tmp_path):
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    incumbent = _model("incumbent", score="1.0")
    candidate = _model("candidate", score="1.04")
    registry.register(incumbent)
    registry.register(candidate)
    registry.promote(incumbent.model_id, _policy(), NOW)
    with pytest.raises(ValueError, match="does not improve"):
        registry.promote(candidate.model_id, _policy(), NOW)


def test_promotion_accepts_improved_candidate_and_records_history(tmp_path):
    path = tmp_path / "registry.json"
    registry = AtomicModelRegistry(path)
    incumbent = _model("incumbent", score="1.0")
    candidate = _model("candidate", score="1.1")
    registry.register(incumbent)
    registry.register(candidate)
    registry.promote(incumbent.model_id, _policy(), NOW)
    registry.promote(candidate.model_id, _policy(), NOW)
    payload = json.loads(path.read_text())
    assert registry.active_model() == candidate
    assert payload["promotion_history"][-1]["previous_model_id"] == "incumbent"
