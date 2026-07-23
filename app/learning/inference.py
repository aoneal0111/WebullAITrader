from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from app.learning.engine import AtomicModelRegistry, RegisteredModel
from app.learning.training import LinearModel, load_linear_model


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    symbol: str
    features: Mapping[str, Decimal | int | float | str]

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("inference symbol is required")
        if not self.features:
            raise ValueError("inference features are required")


@dataclass(frozen=True, slots=True)
class InferenceResult:
    model_id: str
    symbol: str
    prediction: int
    action: str
    score: Decimal
    threshold: Decimal
    confidence: Decimal
    feature_names: tuple[str, ...]
    feature_values: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.prediction not in (0, 1):
            raise ValueError("inference prediction must be binary")
        if self.action not in {"BUY", "HOLD"}:
            raise ValueError("inference action is invalid")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("inference confidence must be between zero and one")


class ActiveModelInferenceEngine:
    """Load and execute the active promoted model with fail-closed validation."""

    def __init__(
        self,
        *,
        registry: AtomicModelRegistry,
        artifact_directory: str | Path,
    ) -> None:
        self._registry = registry
        self._artifact_directory = Path(artifact_directory)
        self._registered_model: RegisteredModel | None = None
        self._linear_model: LinearModel | None = None

    @property
    def active_model_id(self) -> str | None:
        if self._registered_model is None:
            return None
        return self._registered_model.model_id

    def reload(self) -> bool:
        registered = self._registry.active_model()
        if registered is None:
            self._registered_model = None
            self._linear_model = None
            raise ValueError("no active model is available for inference")

        if (
            self._registered_model is not None
            and self._linear_model is not None
            and self._registered_model.model_id == registered.model_id
        ):
            return False

        artifact_path = self._artifact_directory / f"{registered.model_id}.json"
        try:
            raw = artifact_path.read_bytes()
        except OSError as exc:
            raise ValueError("active model artifact is unavailable") from exc

        artifact_hash = hashlib.sha256(raw).hexdigest()
        if artifact_hash != registered.artifact_sha256:
            raise ValueError("active model artifact SHA-256 does not match registry")

        linear_model = load_linear_model(artifact_path)

        if linear_model.feature_names != registered.feature_names:
            raise ValueError("active model artifact features do not match registry")

        self._registered_model = registered
        self._linear_model = linear_model
        return True

    def infer(self, request: InferenceRequest) -> InferenceResult:
        if self._registered_model is None or self._linear_model is None:
            self.reload()

        registered = self._registered_model
        model = self._linear_model
        if registered is None or model is None:
            raise RuntimeError("active model failed to load")

        supplied_names = set(request.features)
        expected_names = set(model.feature_names)

        missing = tuple(name for name in model.feature_names if name not in supplied_names)
        unexpected = tuple(sorted(supplied_names - expected_names))

        if missing:
            raise ValueError(
                "inference request is missing features: " + ", ".join(missing)
            )
        if unexpected:
            raise ValueError(
                "inference request contains unexpected features: "
                + ", ".join(unexpected)
            )

        try:
            feature_values = tuple(
                _decimal(request.features[name]) for name in model.feature_names
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("inference feature value is invalid") from exc

        score = model.score(feature_values)
        prediction = model.predict(feature_values)
        confidence = _confidence(score=score, threshold=model.threshold)

        return InferenceResult(
            model_id=registered.model_id,
            symbol=request.symbol.strip().upper(),
            prediction=prediction,
            action="BUY" if prediction == 1 else "HOLD",
            score=score,
            threshold=model.threshold,
            confidence=confidence,
            feature_names=model.feature_names,
            feature_values=feature_values,
        )


def _decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid inference features")
    resolved = Decimal(str(value))
    if not resolved.is_finite():
        raise ValueError("inference feature must be finite")
    return resolved


def _confidence(*, score: Decimal, threshold: Decimal) -> Decimal:
    """Convert distance from the decision boundary to a deterministic [0, 1) value."""

    margin = abs(score - threshold)
    return margin / (Decimal("1") + margin)
