from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.learning.inference import (
    ActiveModelInferenceEngine,
    InferenceRequest,
    InferenceResult,
)


FeatureBuilder = Callable[
    [Any, Any, int, int],
    Mapping[str, Decimal | int | float | str],
]


class ReloadableInferenceEngine(Protocol):
    @property
    def active_model_id(self) -> str | None: ...

    def reload(self) -> bool: ...

    def infer(self, request: InferenceRequest) -> InferenceResult: ...


@dataclass(frozen=True, slots=True)
class RuntimeInferencePolicy:
    minimum_confidence: Decimal = Decimal("0.60")
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if self.minimum_confidence < 0 or self.minimum_confidence > 1:
            raise ValueError(
                "minimum inference confidence must be between zero and one"
            )


@dataclass(frozen=True, slots=True)
class RuntimeInferenceAudit:
    cycle: int
    symbol_index: int
    symbol: str
    model_id: str | None
    prediction: int | None
    action: str
    score: Decimal | None
    threshold: Decimal | None
    confidence: Decimal | None
    minimum_confidence: Decimal
    feature_names: tuple[str, ...]
    feature_sha256: str | None
    model_reloaded: bool
    allows_order_intent: bool
    reason: str
    error: str | None = None

    def __post_init__(self) -> None:
        if self.cycle < 1:
            raise ValueError("runtime inference cycle must be positive")
        if self.symbol_index < 1:
            raise ValueError("runtime inference symbol index must be positive")

        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("runtime inference symbol is required")
        object.__setattr__(self, "symbol", symbol)

        if self.prediction is not None and self.prediction not in (0, 1):
            raise ValueError("runtime inference prediction must be binary")

        if self.confidence is not None and not (
            Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ValueError(
                "runtime inference confidence must be between zero and one"
            )

        if self.action not in {"BUY", "HOLD", "UNAVAILABLE"}:
            raise ValueError("runtime inference action is invalid")

        if not self.reason.strip():
            raise ValueError("runtime inference reason is required")


class RuntimeInferenceAdapter:
    """Run promoted-model inference without owning strategy or execution logic.

    The adapter reloads only when entering a new runtime cycle. Inference
    failures are converted into immutable audit records rather than leaking
    partial model state into the trading pipeline.
    """

    def __init__(
        self,
        *,
        inference_engine: ReloadableInferenceEngine,
        feature_builder: FeatureBuilder,
        policy: RuntimeInferencePolicy | None = None,
    ) -> None:
        self._inference_engine = inference_engine
        self._feature_builder = feature_builder
        self._policy = policy or RuntimeInferencePolicy()
        self._loaded_cycle: int | None = None
        self._cycle_reloaded = False

    @classmethod
    def from_active_model_engine(
        cls,
        *,
        inference_engine: ActiveModelInferenceEngine,
        feature_builder: FeatureBuilder,
        policy: RuntimeInferencePolicy | None = None,
    ) -> "RuntimeInferenceAdapter":
        return cls(
            inference_engine=inference_engine,
            feature_builder=feature_builder,
            policy=policy,
        )

    @property
    def policy(self) -> RuntimeInferencePolicy:
        return self._policy

    def begin_cycle(self, cycle: int) -> bool:
        if cycle < 1:
            raise ValueError("runtime inference cycle must be positive")

        if self._loaded_cycle == cycle:
            return self._cycle_reloaded

        reloaded = self._inference_engine.reload()
        self._loaded_cycle = cycle
        self._cycle_reloaded = reloaded
        return reloaded

    def evaluate(
        self,
        *,
        snapshot: Any,
        session: Any,
        cycle: int,
        symbol_index: int,
    ) -> RuntimeInferenceAudit:
        if cycle < 1:
            raise ValueError("runtime inference cycle must be positive")
        if symbol_index < 1:
            raise ValueError("runtime inference symbol index must be positive")

        symbol = _symbol(snapshot)

        try:
            reloaded = self.begin_cycle(cycle)
            features = self._feature_builder(
                snapshot,
                session,
                cycle,
                symbol_index,
            )
            request = InferenceRequest(
                symbol=symbol,
                features=features,
            )
            result = self._inference_engine.infer(request)
            return self._audit_result(
                result=result,
                cycle=cycle,
                symbol_index=symbol_index,
                reloaded=reloaded,
            )
        except Exception as exc:
            if not self._policy.fail_closed:
                raise

            return RuntimeInferenceAudit(
                cycle=cycle,
                symbol_index=symbol_index,
                symbol=symbol,
                model_id=self._inference_engine.active_model_id,
                prediction=None,
                action="UNAVAILABLE",
                score=None,
                threshold=None,
                confidence=None,
                minimum_confidence=self._policy.minimum_confidence,
                feature_names=(),
                feature_sha256=None,
                model_reloaded=False,
                allows_order_intent=False,
                reason="inference failed closed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _audit_result(
        self,
        *,
        result: InferenceResult,
        cycle: int,
        symbol_index: int,
        reloaded: bool,
    ) -> RuntimeInferenceAudit:
        confidence_passed = (
            result.confidence >= self._policy.minimum_confidence
        )
        predicts_buy = result.prediction == 1
        allowed = predicts_buy and confidence_passed

        if not predicts_buy:
            reason = "model prediction is HOLD"
        elif not confidence_passed:
            reason = "model confidence is below minimum"
        else:
            reason = "model prediction and confidence permit order intent"

        return RuntimeInferenceAudit(
            cycle=cycle,
            symbol_index=symbol_index,
            symbol=result.symbol,
            model_id=result.model_id,
            prediction=result.prediction,
            action=result.action,
            score=result.score,
            threshold=result.threshold,
            confidence=result.confidence,
            minimum_confidence=self._policy.minimum_confidence,
            feature_names=result.feature_names,
            feature_sha256=_feature_sha256(
                result.feature_names,
                result.feature_values,
            ),
            model_reloaded=reloaded,
            allows_order_intent=allowed,
            reason=reason,
            error=None,
        )


def runtime_inference_audit_payload(
    audit: RuntimeInferenceAudit,
) -> dict[str, Any]:
    """Return a canonical JSON-safe representation for runtime journaling."""

    return {
        "cycle": audit.cycle,
        "symbol_index": audit.symbol_index,
        "symbol": audit.symbol,
        "model_id": audit.model_id,
        "prediction": audit.prediction,
        "action": audit.action,
        "score": _decimal_text(audit.score),
        "threshold": _decimal_text(audit.threshold),
        "confidence": _decimal_text(audit.confidence),
        "minimum_confidence": format(audit.minimum_confidence, "f"),
        "feature_names": list(audit.feature_names),
        "feature_sha256": audit.feature_sha256,
        "model_reloaded": audit.model_reloaded,
        "allows_order_intent": audit.allows_order_intent,
        "reason": audit.reason,
        "error": audit.error,
    }


def _feature_sha256(
    names: tuple[str, ...],
    values: tuple[Decimal, ...],
) -> str:
    payload = {
        "feature_names": list(names),
        "feature_values": [format(value, "f") for value in values],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _symbol(snapshot: Any) -> str:
    value = getattr(snapshot, "symbol", None)
    if value is None:
        raise ValueError("runtime inference snapshot symbol is required")
    symbol = str(value).strip().upper()
    if not symbol:
        raise ValueError("runtime inference snapshot symbol is required")
    return symbol
