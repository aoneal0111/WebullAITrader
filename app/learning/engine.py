from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    dataset_id: str
    created_at: datetime
    source_session_id: str
    source_journal_sha256: str
    records_sha256: str
    record_count: int
    feature_names: tuple[str, ...]
    records_path: Path
    manifest_path: Path

    def __post_init__(self) -> None:
        _require_aware(self.created_at)
        if not self.dataset_id.strip():
            raise ValueError("dataset ID is required")
        if not self.source_session_id.strip():
            raise ValueError("source session ID is required")
        if self.record_count < 1:
            raise ValueError("training dataset must contain at least one record")
        if not self.feature_names or any(not name.strip() for name in self.feature_names):
            raise ValueError("feature names are required")


@dataclass(frozen=True, slots=True)
class ModelEvaluation:
    walk_forward_score: Decimal
    stress_score: Decimal
    max_drawdown: Decimal
    sample_count: int

    def __post_init__(self) -> None:
        if self.sample_count < 1:
            raise ValueError("evaluation sample count must be positive")
        if self.max_drawdown < 0:
            raise ValueError("maximum drawdown cannot be negative")


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    model_id: str
    created_at: datetime
    dataset_id: str
    artifact_sha256: str
    git_commit: str
    feature_names: tuple[str, ...]
    hyperparameters: tuple[tuple[str, str], ...]
    evaluation: ModelEvaluation

    def __post_init__(self) -> None:
        _require_aware(self.created_at)
        for label, value in (
            ("model ID", self.model_id),
            ("dataset ID", self.dataset_id),
            ("artifact SHA-256", self.artifact_sha256),
            ("Git commit", self.git_commit),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if not self.feature_names or any(not item.strip() for item in self.feature_names):
            raise ValueError("model feature names are required")


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_walk_forward_score: Decimal
    minimum_stress_score: Decimal
    maximum_drawdown: Decimal
    minimum_sample_count: int
    required_improvement: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.maximum_drawdown < 0:
            raise ValueError("maximum permitted drawdown cannot be negative")
        if self.minimum_sample_count < 1:
            raise ValueError("minimum sample count must be positive")
        if self.required_improvement < 0:
            raise ValueError("required improvement cannot be negative")

    def rejection_reasons(
        self,
        candidate: RegisteredModel,
        incumbent: RegisteredModel | None,
    ) -> tuple[str, ...]:
        evaluation = candidate.evaluation
        reasons: list[str] = []
        if evaluation.walk_forward_score < self.minimum_walk_forward_score:
            reasons.append("walk-forward score below minimum")
        if evaluation.stress_score < self.minimum_stress_score:
            reasons.append("stress score below minimum")
        if evaluation.max_drawdown > self.maximum_drawdown:
            reasons.append("maximum drawdown exceeds limit")
        if evaluation.sample_count < self.minimum_sample_count:
            reasons.append("evaluation sample count below minimum")
        if incumbent is not None:
            required = incumbent.evaluation.walk_forward_score + self.required_improvement
            if evaluation.walk_forward_score < required:
                reasons.append("walk-forward score does not improve on active model")
        return tuple(reasons)


class ImmutableJournalDatasetExporter:
    """Export a canonical immutable JSONL training dataset from a runtime journal."""

    FEATURE_NAMES = (
        "cycle",
        "symbol_ordinal",
        "session_decisions_processed",
        "session_orders_attempted",
        "session_orders_filled",
        "session_orders_rejected",
        "session_realized_pnl",
        "session_unrealized_pnl",
        "session_current_equity",
        "session_current_drawdown",
    )

    def export(
        self,
        *,
        journal_path: str | Path,
        output_directory: str | Path,
        created_at: datetime,
    ) -> TrainingDataset:
        _require_aware(created_at)
        source_path = Path(journal_path)
        raw = source_path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        try:
            journal = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("runtime journal is unreadable") from exc
        self._validate_journal(journal)

        records = tuple(self._records(journal))
        if not records:
            raise ValueError("runtime journal contains no training records")
        canonical_lines = tuple(_canonical_json(record) for record in records)
        records_bytes = ("\n".join(canonical_lines) + "\n").encode("utf-8")
        records_hash = hashlib.sha256(records_bytes).hexdigest()
        dataset_id = f"dataset-{records_hash[:16]}"

        destination = Path(output_directory)
        destination.mkdir(parents=True, exist_ok=True)
        records_path = destination / f"{dataset_id}.jsonl"
        manifest_path = destination / f"{dataset_id}.manifest.json"
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "created_at": created_at.isoformat(),
            "source_session_id": journal["session_id"],
            "source_journal_sha256": source_hash,
            "records_sha256": records_hash,
            "record_count": len(records),
            "feature_names": list(self.FEATURE_NAMES),
            "records_file": records_path.name,
        }
        _write_immutable(records_path, records_bytes)
        _write_immutable(manifest_path, (_canonical_json(manifest) + "\n").encode("utf-8"))
        return TrainingDataset(
            dataset_id=dataset_id,
            created_at=created_at,
            source_session_id=journal["session_id"],
            source_journal_sha256=source_hash,
            records_sha256=records_hash,
            record_count=len(records),
            feature_names=self.FEATURE_NAMES,
            records_path=records_path,
            manifest_path=manifest_path,
        )

    @staticmethod
    def _validate_journal(journal: Any) -> None:
        if not isinstance(journal, dict) or journal.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported runtime journal schema")
        if not isinstance(journal.get("session_id"), str) or not journal["session_id"].strip():
            raise ValueError("runtime journal session ID is invalid")
        cycles = journal.get("cycles")
        if not isinstance(cycles, list):
            raise ValueError("runtime journal cycles must be a list")
        if any(not isinstance(cycle, dict) or cycle.get("cycle") != index for index, cycle in enumerate(cycles, 1)):
            raise ValueError("runtime journal cycle sequence is invalid")

    def _records(self, journal: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for cycle in journal["cycles"]:
            symbols = cycle.get("symbols")
            decisions = cycle.get("decisions")
            statistics = cycle.get("session_statistics")
            if not isinstance(symbols, list) or not isinstance(decisions, list) or len(symbols) != len(decisions):
                raise ValueError("runtime journal symbols and decisions must align")
            if not isinstance(statistics, dict):
                raise ValueError("runtime journal session statistics are invalid")
            for ordinal, (symbol, decision) in enumerate(zip(symbols, decisions, strict=True), 1):
                if not isinstance(symbol, str) or not symbol.strip() or not isinstance(decision, dict):
                    raise ValueError("runtime journal decision record is invalid")
                yield {
                    "record_id": f"{journal['session_id']}:{cycle['cycle']}:{symbol.strip().upper()}",
                    "session_id": journal["session_id"],
                    "timestamp": cycle.get("timestamp"),
                    "symbol": symbol.strip().upper(),
                    "decision": decision,
                    "features": {
                        "cycle": cycle["cycle"],
                        "symbol_ordinal": ordinal,
                        "session_decisions_processed": statistics.get("decisions_processed"),
                        "session_orders_attempted": statistics.get("orders_attempted"),
                        "session_orders_filled": statistics.get("orders_filled"),
                        "session_orders_rejected": statistics.get("orders_rejected"),
                        "session_realized_pnl": statistics.get("realized_pnl"),
                        "session_unrealized_pnl": statistics.get("unrealized_pnl"),
                        "session_current_equity": statistics.get("current_equity"),
                        "session_current_drawdown": statistics.get("current_drawdown"),
                    },
                }


class AtomicModelRegistry:
    """Version models and promote only candidates that satisfy an explicit gate."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def register(self, model: RegisteredModel) -> None:
        payload = self._load()
        if any(item["model_id"] == model.model_id for item in payload["models"]):
            raise ValueError(f"model already registered: {model.model_id}")
        payload["models"].append(_model_payload(model))
        self._write(payload)

    def active_model(self) -> RegisteredModel | None:
        payload = self._load()
        active_id = payload["active_model_id"]
        if active_id is None:
            return None
        return _model_from_payload(self._find(payload, active_id))

    def promote(self, model_id: str, policy: PromotionPolicy, promoted_at: datetime) -> RegisteredModel:
        _require_aware(promoted_at)
        payload = self._load()
        candidate = _model_from_payload(self._find(payload, model_id))
        incumbent = None
        if payload["active_model_id"] is not None:
            incumbent = _model_from_payload(self._find(payload, payload["active_model_id"]))
        reasons = policy.rejection_reasons(candidate, incumbent)
        if reasons:
            raise ValueError("model promotion rejected: " + "; ".join(reasons))
        payload["active_model_id"] = candidate.model_id
        payload["promotion_history"].append(
            {
                "model_id": candidate.model_id,
                "promoted_at": promoted_at.isoformat(),
                "previous_model_id": incumbent.model_id if incumbent else None,
            }
        )
        self._write(payload)
        return candidate

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "schema_version": SCHEMA_VERSION,
                "active_model_id": None,
                "models": [],
                "promotion_history": [],
            }
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("model registry is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported model registry schema")
        if not isinstance(payload.get("models"), list) or not isinstance(payload.get("promotion_history"), list):
            raise ValueError("model registry structure is invalid")
        ids = [item.get("model_id") for item in payload["models"] if isinstance(item, dict)]
        if len(ids) != len(payload["models"]) or len(ids) != len(set(ids)):
            raise ValueError("model registry model IDs are invalid")
        if payload.get("active_model_id") is not None and payload["active_model_id"] not in ids:
            raise ValueError("active model is not registered")
        return payload

    @staticmethod
    def _find(payload: dict[str, Any], model_id: str) -> dict[str, Any]:
        for item in payload["models"]:
            if item["model_id"] == model_id:
                return item
        raise KeyError(f"model is not registered: {model_id}")

    def _write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = (_canonical_json(payload) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self._path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _model_payload(model: RegisteredModel) -> dict[str, Any]:
    return {
        "model_id": model.model_id,
        "created_at": model.created_at.isoformat(),
        "dataset_id": model.dataset_id,
        "artifact_sha256": model.artifact_sha256,
        "git_commit": model.git_commit,
        "feature_names": list(model.feature_names),
        "hyperparameters": [[key, value] for key, value in model.hyperparameters],
        "evaluation": {
            "walk_forward_score": format(model.evaluation.walk_forward_score, "f"),
            "stress_score": format(model.evaluation.stress_score, "f"),
            "max_drawdown": format(model.evaluation.max_drawdown, "f"),
            "sample_count": model.evaluation.sample_count,
        },
    }


def _model_from_payload(payload: dict[str, Any]) -> RegisteredModel:
    evaluation = payload["evaluation"]
    return RegisteredModel(
        model_id=payload["model_id"],
        created_at=datetime.fromisoformat(payload["created_at"]),
        dataset_id=payload["dataset_id"],
        artifact_sha256=payload["artifact_sha256"],
        git_commit=payload["git_commit"],
        feature_names=tuple(payload["feature_names"]),
        hyperparameters=tuple((str(key), str(value)) for key, value in payload["hyperparameters"]),
        evaluation=ModelEvaluation(
            walk_forward_score=Decimal(evaluation["walk_forward_score"]),
            stress_score=Decimal(evaluation["stress_score"]),
            max_drawdown=Decimal(evaluation["max_drawdown"]),
            sample_count=evaluation["sample_count"],
        ),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"immutable artifact already exists with different content: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise
        finally:
            os.unlink(temporary_name)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
