from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.learning.inference import InferenceRequest, InferenceResult
from app.operations.learning_runtime import (
    RuntimeInferenceAdapter,
    RuntimeInferencePolicy,
    runtime_inference_audit_payload,
)


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    feature_a: Decimal
    feature_b: Decimal


class FakeInferenceEngine:
    def __init__(
        self,
        *,
        prediction: int = 1,
        confidence: Decimal = Decimal("0.80"),
        fail_reload: bool = False,
        fail_infer: bool = False,
    ) -> None:
        self.prediction = prediction
        self.confidence = confidence
        self.fail_reload = fail_reload
        self.fail_infer = fail_infer
        self.reload_calls = 0
        self.infer_calls = 0
        self.active_model_id = "model-test"

    def reload(self) -> bool:
        self.reload_calls += 1
        if self.fail_reload:
            raise ValueError("reload failed")
        return self.reload_calls == 1

    def infer(self, request: InferenceRequest) -> InferenceResult:
        self.infer_calls += 1
        if self.fail_infer:
            raise ValueError("inference failed")

        values = tuple(
            Decimal(str(request.features[name]))
            for name in ("feature_a", "feature_b")
        )
        score = values[0] - values[1]

        return InferenceResult(
            model_id="model-test",
            symbol=request.symbol.strip().upper(),
            prediction=self.prediction,
            action="BUY" if self.prediction == 1 else "HOLD",
            score=score,
            threshold=Decimal("0"),
            confidence=self.confidence,
            feature_names=("feature_a", "feature_b"),
            feature_values=values,
        )


def _features(
    snapshot: Snapshot,
    session: object,
    cycle: int,
    symbol_index: int,
) -> dict[str, Decimal]:
    del session
    del cycle
    del symbol_index
    return {
        "feature_a": snapshot.feature_a,
        "feature_b": snapshot.feature_b,
    }


def _adapter(
    engine: FakeInferenceEngine,
    *,
    minimum_confidence: str = "0.60",
    fail_closed: bool = True,
) -> RuntimeInferenceAdapter:
    return RuntimeInferenceAdapter(
        inference_engine=engine,
        feature_builder=_features,
        policy=RuntimeInferencePolicy(
            minimum_confidence=Decimal(minimum_confidence),
            fail_closed=fail_closed,
        ),
    )


def test_policy_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="between zero and one"):
        RuntimeInferencePolicy(minimum_confidence=Decimal("1.01"))


def test_model_reloads_once_per_cycle() -> None:
    engine = FakeInferenceEngine()
    adapter = _adapter(engine)
    snapshot = Snapshot("aapl", Decimal("3"), Decimal("1"))

    adapter.evaluate(
        snapshot=snapshot,
        session=object(),
        cycle=1,
        symbol_index=1,
    )
    adapter.evaluate(
        snapshot=snapshot,
        session=object(),
        cycle=1,
        symbol_index=2,
    )
    adapter.evaluate(
        snapshot=snapshot,
        session=object(),
        cycle=2,
        symbol_index=1,
    )

    assert engine.reload_calls == 2
    assert engine.infer_calls == 3


def test_buy_with_sufficient_confidence_allows_order_intent() -> None:
    engine = FakeInferenceEngine(
        prediction=1,
        confidence=Decimal("0.80"),
    )
    audit = _adapter(engine).evaluate(
        snapshot=Snapshot("aapl", Decimal("3"), Decimal("1")),
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    assert audit.symbol == "AAPL"
    assert audit.action == "BUY"
    assert audit.allows_order_intent is True
    assert audit.feature_sha256 is not None
    assert audit.error is None


def test_buy_below_confidence_threshold_is_vetoed() -> None:
    engine = FakeInferenceEngine(
        prediction=1,
        confidence=Decimal("0.59"),
    )
    audit = _adapter(engine).evaluate(
        snapshot=Snapshot("aapl", Decimal("3"), Decimal("1")),
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    assert audit.action == "BUY"
    assert audit.allows_order_intent is False
    assert audit.reason == "model confidence is below minimum"


def test_hold_prediction_is_vetoed() -> None:
    engine = FakeInferenceEngine(
        prediction=0,
        confidence=Decimal("0.90"),
    )
    audit = _adapter(engine).evaluate(
        snapshot=Snapshot("aapl", Decimal("1"), Decimal("3")),
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    assert audit.action == "HOLD"
    assert audit.allows_order_intent is False
    assert audit.reason == "model prediction is HOLD"


def test_inference_failure_fails_closed() -> None:
    engine = FakeInferenceEngine(fail_infer=True)
    audit = _adapter(engine).evaluate(
        snapshot=Snapshot("aapl", Decimal("3"), Decimal("1")),
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    assert audit.action == "UNAVAILABLE"
    assert audit.allows_order_intent is False
    assert audit.reason == "inference failed closed"
    assert audit.error == "ValueError: inference failed"


def test_failure_can_be_propagated_when_configured() -> None:
    engine = FakeInferenceEngine(fail_reload=True)
    adapter = _adapter(engine, fail_closed=False)

    with pytest.raises(ValueError, match="reload failed"):
        adapter.evaluate(
            snapshot=Snapshot("aapl", Decimal("3"), Decimal("1")),
            session=object(),
            cycle=1,
            symbol_index=1,
        )


def test_audit_payload_is_deterministic_and_json_safe() -> None:
    engine = FakeInferenceEngine()
    adapter = _adapter(engine)
    snapshot = Snapshot("aapl", Decimal("3.00"), Decimal("1.00"))

    first = adapter.evaluate(
        snapshot=snapshot,
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    second_engine = FakeInferenceEngine()
    second = _adapter(second_engine).evaluate(
        snapshot=snapshot,
        session=object(),
        cycle=1,
        symbol_index=1,
    )

    first_payload = runtime_inference_audit_payload(first)
    second_payload = runtime_inference_audit_payload(second)

    assert first_payload == second_payload
    assert first_payload["score"] == "2.00"
    assert first_payload["confidence"] == "0.80"
    assert first_payload["feature_names"] == [
        "feature_a",
        "feature_b",
    ]
