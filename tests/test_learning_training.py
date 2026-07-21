from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.learning import (
    AtomicModelRegistry,
    DatasetReader,
    ExpandingWindowSplitter,
    ImmutableJournalDatasetExporter,
    OfflineTrainingPipeline,
    StressEvaluator,
    load_linear_model,
)

NOW = datetime(2026, 7, 20, 18, 0, tzinfo=UTC)


def _dataset(tmp_path, actions=("HOLD", "BUY", "HOLD", "BUY", "HOLD", "BUY", "HOLD", "BUY")):
    cycles = []
    for index, action in enumerate(actions, 1):
        cycles.append(
            {
                "cycle": index,
                "timestamp": (NOW + timedelta(minutes=index)).isoformat(),
                "symbols": ["AAPL"],
                "decisions": [{"symbol": "AAPL", "action": action}],
                "session_statistics": {
                    "current_equity": str(10000 + index * (1 if action == "BUY" else -1)),
                    "current_drawdown": str(index % 3),
                    "realized_pnl": str(index),
                    "unrealized_pnl": str(index / 2),
                    "decisions_processed": index,
                    "orders_attempted": index // 2,
                    "orders_filled": index // 2,
                    "orders_rejected": 0,
                },
            }
        )
    journal = tmp_path / "journal.json"
    journal.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "session_id": "paper-training",
                "cycles": cycles,
                "latest_analytics": None,
            }
        ),
        encoding="utf-8",
    )
    return ImmutableJournalDatasetExporter().export(
        journal_path=journal,
        output_directory=tmp_path / "datasets",
        created_at=NOW,
    )


def test_dataset_reader_validates_hash_and_reads_binary_targets(tmp_path):
    dataset = _dataset(tmp_path)
    records = DatasetReader().read(dataset)
    assert len(records) == 8
    assert [record.target for record in records[:4]] == [0, 1, 0, 1]
    assert records[0].timestamp < records[-1].timestamp


def test_dataset_reader_rejects_tampered_records(tmp_path):
    dataset = _dataset(tmp_path)
    dataset.records_path.write_text(dataset.records_path.read_text() + "{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        DatasetReader().read(dataset)


def test_expanding_window_splits_never_train_on_future_records():
    folds = ExpandingWindowSplitter(minimum_train_size=4, test_size=2, step_size=2).split(8)
    assert len(folds) == 2
    assert folds[0].train_indices == (0, 1, 2, 3)
    assert folds[0].test_indices == (4, 5)
    assert max(folds[1].train_indices) < min(folds[1].test_indices)


def test_splitter_rejects_dataset_too_small():
    with pytest.raises(ValueError, match="too small"):
        ExpandingWindowSplitter(minimum_train_size=4, test_size=2).split(5)


def test_training_pipeline_is_deterministic_and_registers_model(tmp_path):
    dataset = _dataset(tmp_path)
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    pipeline = OfflineTrainingPipeline(
        splitter=ExpandingWindowSplitter(minimum_train_size=4, test_size=2, step_size=2)
    )
    first = pipeline.run(
        dataset=dataset,
        artifact_directory=tmp_path / "models",
        created_at=NOW,
        git_commit="6af934c",
        registry=registry,
    )
    second = pipeline.run(
        dataset=dataset,
        artifact_directory=tmp_path / "models",
        created_at=NOW,
        git_commit="6af934c",
    )
    assert first.model.model_id == second.model.model_id
    assert first.artifact_path.read_bytes() == second.artifact_path.read_bytes()
    assert first.model.artifact_sha256 == hashlib.sha256(first.artifact_path.read_bytes()).hexdigest()
    assert registry.active_model() is None


def test_training_artifact_can_be_loaded_for_inference(tmp_path):
    dataset = _dataset(tmp_path)
    run = OfflineTrainingPipeline(
        splitter=ExpandingWindowSplitter(minimum_train_size=4, test_size=2)
    ).run(
        dataset=dataset,
        artifact_directory=tmp_path / "models",
        created_at=NOW,
        git_commit="6af934c",
    )
    model = load_linear_model(run.artifact_path)
    record = DatasetReader().read(dataset)[0]
    assert model.feature_names == dataset.feature_names
    assert model.predict(record.features) in (0, 1)


def test_training_metrics_cover_only_walk_forward_test_samples(tmp_path):
    dataset = _dataset(tmp_path)
    run = OfflineTrainingPipeline(
        splitter=ExpandingWindowSplitter(minimum_train_size=4, test_size=2, step_size=2)
    ).run(
        dataset=dataset,
        artifact_directory=tmp_path / "models",
        created_at=NOW,
        git_commit="6af934c",
    )
    assert run.model.evaluation.sample_count == 4
    assert Decimal("0") <= run.model.evaluation.walk_forward_score <= Decimal("1")
    assert Decimal("0") <= run.model.evaluation.stress_score <= Decimal("1")
    assert Decimal("0") <= run.model.evaluation.max_drawdown <= Decimal("1")


def test_stress_evaluator_validates_perturbation():
    with pytest.raises(ValueError, match="between zero and one"):
        StressEvaluator(perturbation_fraction=Decimal("1"))
