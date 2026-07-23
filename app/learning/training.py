from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.learning.engine import (
    AtomicModelRegistry,
    ModelEvaluation,
    RegisteredModel,
    TrainingDataset,
    _canonical_json,
    _require_aware,
    _write_immutable,
)


@dataclass(frozen=True, slots=True)
class LearningRecord:
    record_id: str
    timestamp: datetime
    symbol: str
    features: tuple[Decimal, ...]
    target: int

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        if not self.record_id.strip() or not self.symbol.strip():
            raise ValueError("learning record identity is required")
        if not self.features:
            raise ValueError("learning record features are required")
        if self.target not in (0, 1):
            raise ValueError("learning target must be binary")


@dataclass(frozen=True, slots=True)
class TrainingFold:
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.train_indices or not self.test_indices:
            raise ValueError("training folds require train and test indices")
        if max(self.train_indices) >= min(self.test_indices):
            raise ValueError("walk-forward folds cannot train on future records")


@dataclass(frozen=True, slots=True)
class LinearModel:
    feature_names: tuple[str, ...]
    weights: tuple[Decimal, ...]
    threshold: Decimal
    positive_rate: Decimal

    def __post_init__(self) -> None:
        if not self.feature_names or len(self.feature_names) != len(self.weights):
            raise ValueError("linear model feature names and weights must align")
        if self.positive_rate < 0 or self.positive_rate > 1:
            raise ValueError("positive rate must be between zero and one")

    def score(self, features: Sequence[Decimal]) -> Decimal:
        if len(features) != len(self.weights):
            raise ValueError("feature vector does not match model")
        return sum((weight * value for weight, value in zip(self.weights, features, strict=True)), Decimal("0"))

    def predict(self, features: Sequence[Decimal]) -> int:
        return int(self.score(features) >= self.threshold)


@dataclass(frozen=True, slots=True)
class TrainingRun:
    model: RegisteredModel
    artifact_path: Path
    folds: tuple[TrainingFold, ...]


class DatasetReader:
    """Read and validate immutable learning records in canonical dataset order."""

    def read(self, dataset: TrainingDataset) -> tuple[LearningRecord, ...]:
        raw = dataset.records_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != dataset.records_sha256:
            raise ValueError("training dataset records hash does not match manifest")
        records: list[LearningRecord] = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            try:
                payload = json.loads(line)
                records.append(self._record(payload, dataset.feature_names))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"training record {line_number} is invalid") from exc
        if len(records) != dataset.record_count:
            raise ValueError("training dataset record count does not match manifest")
        ordered = tuple(sorted(records, key=lambda item: (item.timestamp, item.record_id)))
        if tuple(records) != ordered:
            raise ValueError("training dataset records are not in deterministic time order")
        return ordered

    @staticmethod
    def _record(payload: dict[str, Any], feature_names: tuple[str, ...]) -> LearningRecord:
        feature_payload = payload["features"]
        if not isinstance(feature_payload, dict):
            raise ValueError("features must be an object")
        features = tuple(_decimal(feature_payload[name]) for name in feature_names)
        decision = payload["decision"]
        if not isinstance(decision, dict):
            raise ValueError("decision must be an object")
        action = str(decision.get("action", "")).strip().upper()
        if action not in {"BUY", "HOLD", "SELL"}:
            raise ValueError("decision action is unsupported")
        return LearningRecord(
            record_id=str(payload["record_id"]),
            timestamp=datetime.fromisoformat(str(payload["timestamp"])),
            symbol=str(payload["symbol"]).strip().upper(),
            features=features,
            target=int(action == "BUY"),
        )


class ExpandingWindowSplitter:
    def __init__(self, *, minimum_train_size: int, test_size: int, step_size: int | None = None) -> None:
        if minimum_train_size < 2:
            raise ValueError("minimum training size must be at least two")
        if test_size < 1:
            raise ValueError("test size must be positive")
        resolved_step = test_size if step_size is None else step_size
        if resolved_step < 1:
            raise ValueError("step size must be positive")
        self.minimum_train_size = minimum_train_size
        self.test_size = test_size
        self.step_size = resolved_step

    def split(self, record_count: int) -> tuple[TrainingFold, ...]:
        folds: list[TrainingFold] = []
        train_end = self.minimum_train_size
        while train_end + self.test_size <= record_count:
            folds.append(
                TrainingFold(
                    train_indices=tuple(range(train_end)),
                    test_indices=tuple(range(train_end, train_end + self.test_size)),
                )
            )
            train_end += self.step_size
        if not folds:
            raise ValueError("dataset is too small for walk-forward validation")
        return tuple(folds)


class DeterministicLinearTrainer:
    """Fit a deterministic nearest-centroid linear classifier without random state."""

    def fit(self, records: Sequence[LearningRecord], feature_names: tuple[str, ...]) -> LinearModel:
        if len(records) < 2:
            raise ValueError("at least two records are required for training")
        positives = [record for record in records if record.target == 1]
        negatives = [record for record in records if record.target == 0]
        if not positives or not negatives:
            constant = Decimal("1") if positives else Decimal("0")
            return LinearModel(
                feature_names=feature_names,
                weights=tuple(Decimal("0") for _ in feature_names),
                threshold=Decimal("-1") if constant else Decimal("1"),
                positive_rate=Decimal(len(positives)) / Decimal(len(records)),
            )
        positive_mean = _mean_vector(positives)
        negative_mean = _mean_vector(negatives)
        weights = tuple(p - n for p, n in zip(positive_mean, negative_mean, strict=True))
        positive_projection = _dot(weights, positive_mean)
        negative_projection = _dot(weights, negative_mean)
        return LinearModel(
            feature_names=feature_names,
            weights=weights,
            threshold=(positive_projection + negative_projection) / Decimal("2"),
            positive_rate=Decimal(len(positives)) / Decimal(len(records)),
        )


class WalkForwardEvaluator:
    def __init__(self, trainer: DeterministicLinearTrainer | None = None) -> None:
        self._trainer = trainer or DeterministicLinearTrainer()

    def evaluate(
        self,
        records: Sequence[LearningRecord],
        feature_names: tuple[str, ...],
        folds: Sequence[TrainingFold],
    ) -> tuple[Decimal, int, Decimal]:
        outcomes: list[int] = []
        for fold in folds:
            model = self._trainer.fit([records[index] for index in fold.train_indices], feature_names)
            for index in fold.test_indices:
                outcomes.append(int(model.predict(records[index].features) == records[index].target))
        score = Decimal(sum(outcomes)) / Decimal(len(outcomes))
        return score, len(outcomes), _error_drawdown(outcomes)


class StressEvaluator:
    def __init__(self, *, perturbation_fraction: Decimal = Decimal("0.05")) -> None:
        if perturbation_fraction < 0 or perturbation_fraction >= 1:
            raise ValueError("perturbation fraction must be between zero and one")
        self.perturbation_fraction = perturbation_fraction

    def evaluate(self, model: LinearModel, records: Sequence[LearningRecord]) -> Decimal:
        if not records:
            raise ValueError("stress evaluation requires records")
        scenario_scores: list[Decimal] = []
        for multiplier in (Decimal("1") - self.perturbation_fraction, Decimal("1") + self.perturbation_fraction):
            correct = 0
            for record in records:
                stressed = tuple(value * multiplier for value in record.features)
                correct += int(model.predict(stressed) == record.target)
            scenario_scores.append(Decimal(correct) / Decimal(len(records)))
        return min(scenario_scores)


class OfflineTrainingPipeline:
    """Train, evaluate, persist, and optionally register one deterministic candidate model."""

    def __init__(
        self,
        *,
        splitter: ExpandingWindowSplitter,
        reader: DatasetReader | None = None,
        trainer: DeterministicLinearTrainer | None = None,
        stress_evaluator: StressEvaluator | None = None,
    ) -> None:
        self.reader = reader or DatasetReader()
        self.trainer = trainer or DeterministicLinearTrainer()
        self.splitter = splitter
        self.stress_evaluator = stress_evaluator or StressEvaluator()

    def run(
        self,
        *,
        dataset: TrainingDataset,
        artifact_directory: str | Path,
        created_at: datetime,
        git_commit: str,
        registry: AtomicModelRegistry | None = None,
    ) -> TrainingRun:
        _require_aware(created_at)
        if not git_commit.strip():
            raise ValueError("Git commit is required")
        records = self.reader.read(dataset)
        folds = self.splitter.split(len(records))
        walk_score, sample_count, max_drawdown = WalkForwardEvaluator(self.trainer).evaluate(
            records, dataset.feature_names, folds
        )
        final_model = self.trainer.fit(records, dataset.feature_names)
        stress_score = self.stress_evaluator.evaluate(final_model, records)
        artifact_payload = {
            "schema_version": "1",
            "algorithm": "deterministic-nearest-centroid-linear",
            "dataset_id": dataset.dataset_id,
            "feature_names": list(final_model.feature_names),
            "weights": [format(value, "f") for value in final_model.weights],
            "threshold": format(final_model.threshold, "f"),
            "positive_rate": format(final_model.positive_rate, "f"),
            "evaluation": {
                "walk_forward_score": format(walk_score, "f"),
                "stress_score": format(stress_score, "f"),
                "max_drawdown": format(max_drawdown, "f"),
                "sample_count": sample_count,
            },
        }
        artifact_bytes = (_canonical_json(artifact_payload) + "\n").encode("utf-8")
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        model_id = f"model-{artifact_hash[:16]}"
        destination = Path(artifact_directory)
        destination.mkdir(parents=True, exist_ok=True)
        artifact_path = destination / f"{model_id}.json"
        _write_immutable(artifact_path, artifact_bytes)
        registered = RegisteredModel(
            model_id=model_id,
            created_at=created_at,
            dataset_id=dataset.dataset_id,
            artifact_sha256=artifact_hash,
            git_commit=git_commit.strip(),
            feature_names=dataset.feature_names,
            hyperparameters=(
                ("algorithm", "deterministic-nearest-centroid-linear"),
                ("minimum_train_size", str(self.splitter.minimum_train_size)),
                ("test_size", str(self.splitter.test_size)),
                ("step_size", str(self.splitter.step_size)),
                ("stress_perturbation_fraction", format(self.stress_evaluator.perturbation_fraction, "f")),
            ),
            evaluation=ModelEvaluation(
                walk_forward_score=walk_score,
                stress_score=stress_score,
                max_drawdown=max_drawdown,
                sample_count=sample_count,
            ),
        )
        if registry is not None:
            registry.register(registered)
        return TrainingRun(model=registered, artifact_path=artifact_path, folds=folds)


def load_linear_model(path: str | Path) -> LinearModel:
    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw)
        if payload.get("schema_version") != "1" or payload.get("algorithm") != "deterministic-nearest-centroid-linear":
            raise ValueError
        return LinearModel(
            feature_names=tuple(str(item) for item in payload["feature_names"]),
            weights=tuple(Decimal(str(item)) for item in payload["weights"]),
            threshold=Decimal(str(payload["threshold"])),
            positive_rate=Decimal(str(payload["positive_rate"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("model artifact is invalid") from exc


def _decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("feature value must be numeric")
    return Decimal(str(value))


def _mean_vector(records: Sequence[LearningRecord]) -> tuple[Decimal, ...]:
    count = Decimal(len(records))
    return tuple(sum((record.features[index] for record in records), Decimal("0")) / count for index in range(len(records[0].features)))


def _dot(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal:
    return sum((a * b for a, b in zip(left, right, strict=True)), Decimal("0"))


def _error_drawdown(outcomes: Iterable[int]) -> Decimal:
    equity = Decimal("0")
    peak = Decimal("0")
    maximum = Decimal("0")
    for correct in outcomes:
        equity += Decimal("1") if correct else Decimal("-1")
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    count = len(tuple(outcomes)) if not isinstance(outcomes, Sequence) else len(outcomes)
    return maximum / Decimal(max(count, 1))
