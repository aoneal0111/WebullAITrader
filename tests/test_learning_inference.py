from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.learning.engine import (
    AtomicModelRegistry,
    ModelEvaluation,
    PromotionPolicy,
    RegisteredModel,
)
from app.learning.inference import (
    ActiveModelInferenceEngine,
    InferenceRequest,
)


FEATURE_NAMES = ("feature_a", "feature_b")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _artifact(
    directory: Path,
    *,
    weights: tuple[str, str] = ("1", "-1"),
    threshold: str = "0",
) -> tuple[Path, str, str]:
    payload = {
        "schema_version": "1",
        "algorithm": "deterministic-nearest-centroid-linear",
        "dataset_id": "dataset-test",
        "feature_names": list(FEATURE_NAMES),
        "weights": list(weights),
        "threshold": threshold,
        "positive_rate": "0.5",
        "evaluation": {
            "walk_forward_score": "1",
            "stress_score": "1",
            "max_drawdown": "0",
            "sample_count": 10,
        },
    }
    raw = (_canonical_json(payload) + "\n").encode("utf-8")
    artifact_hash = hashlib.sha256(raw).hexdigest()
    model_id = f"model-{artifact_hash[:16]}"
    path = directory / f"{model_id}.json"
    path.write_bytes(raw)
    return path, model_id, artifact_hash


def _registered_model(model_id: str, artifact_hash: str) -> RegisteredModel:
    return RegisteredModel(
        model_id=model_id,
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        dataset_id="dataset-test",
        artifact_sha256=artifact_hash,
        git_commit="test-commit",
        feature_names=FEATURE_NAMES,
        hyperparameters=(("algorithm", "test"),),
        evaluation=ModelEvaluation(
            walk_forward_score=Decimal("1"),
            stress_score=Decimal("1"),
            max_drawdown=Decimal("0"),
            sample_count=10,
        ),
    )


def _promote(registry: AtomicModelRegistry, model: RegisteredModel) -> None:
    registry.register(model)
    registry.promote(
        model.model_id,
        PromotionPolicy(
            minimum_walk_forward_score=Decimal("0"),
            minimum_stress_score=Decimal("0"),
            maximum_drawdown=Decimal("1"),
            minimum_sample_count=1,
        ),
        datetime(2026, 7, 20, 1, tzinfo=timezone.utc),
    )


def _engine(tmp_path: Path) -> ActiveModelInferenceEngine:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    _, model_id, artifact_hash = _artifact(artifact_directory)

    registry = AtomicModelRegistry(tmp_path / "registry.json")
    _promote(registry, _registered_model(model_id, artifact_hash))

    return ActiveModelInferenceEngine(
        registry=registry,
        artifact_directory=artifact_directory,
    )


def test_active_promoted_model_loads(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    assert engine.reload() is True
    assert engine.active_model_id is not None
    assert engine.reload() is False


def test_no_active_model_is_rejected(tmp_path: Path) -> None:
    engine = ActiveModelInferenceEngine(
        registry=AtomicModelRegistry(tmp_path / "registry.json"),
        artifact_directory=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="no active model"):
        engine.reload()


def test_missing_active_artifact_is_rejected(tmp_path: Path) -> None:
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    model = _registered_model("model-missing", "0" * 64)
    _promote(registry, model)

    engine = ActiveModelInferenceEngine(
        registry=registry,
        artifact_directory=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="artifact is unavailable"):
        engine.reload()


def test_tampered_active_artifact_is_rejected(tmp_path: Path) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    path, model_id, artifact_hash = _artifact(artifact_directory)

    registry = AtomicModelRegistry(tmp_path / "registry.json")
    _promote(registry, _registered_model(model_id, artifact_hash))

    path.write_text('{"tampered":true}\n', encoding="utf-8")

    engine = ActiveModelInferenceEngine(
        registry=registry,
        artifact_directory=artifact_directory,
    )

    with pytest.raises(ValueError, match="SHA-256"):
        engine.reload()


def test_missing_feature_is_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    with pytest.raises(ValueError, match="missing features: feature_b"):
        engine.infer(
            InferenceRequest(
                symbol="aapl",
                features={"feature_a": Decimal("2")},
            )
        )


def test_unexpected_feature_is_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    with pytest.raises(ValueError, match="unexpected features: feature_c"):
        engine.infer(
            InferenceRequest(
                symbol="aapl",
                features={
                    "feature_a": Decimal("2"),
                    "feature_b": Decimal("1"),
                    "feature_c": Decimal("0"),
                },
            )
        )


def test_prediction_and_confidence_are_deterministic(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    request = InferenceRequest(
        symbol="aapl",
        features={
            "feature_b": Decimal("1"),
            "feature_a": Decimal("3"),
        },
    )

    first = engine.infer(request)
    second = engine.infer(request)

    assert first == second
    assert first.symbol == "AAPL"
    assert first.prediction == 1
    assert first.action == "BUY"
    assert first.feature_names == FEATURE_NAMES
    assert first.feature_values == (Decimal("3"), Decimal("1"))
    assert Decimal("0") <= first.confidence <= Decimal("1")


def test_registry_promotion_is_loaded_after_reload(tmp_path: Path) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()

    _, first_id, first_hash = _artifact(
        artifact_directory,
        weights=("1", "-1"),
        threshold="0",
    )
    registry = AtomicModelRegistry(tmp_path / "registry.json")
    first_model = _registered_model(first_id, first_hash)
    _promote(registry, first_model)

    engine = ActiveModelInferenceEngine(
        registry=registry,
        artifact_directory=artifact_directory,
    )
    engine.reload()
    assert engine.active_model_id == first_id

    _, second_id, second_hash = _artifact(
        artifact_directory,
        weights=("-1", "1"),
        threshold="0",
    )
    second_model = _registered_model(second_id, second_hash)
    registry.register(second_model)
    registry.promote(
        second_id,
        PromotionPolicy(
            minimum_walk_forward_score=Decimal("0"),
            minimum_stress_score=Decimal("0"),
            maximum_drawdown=Decimal("1"),
            minimum_sample_count=1,
        ),
        datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
    )

    assert engine.reload() is True
    assert engine.active_model_id == second_id
