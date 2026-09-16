from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.catalysts.composition import build_catalyst_providers
from app.catalysts.models import CatalystEvidence
from app.catalysts.sec_shadow_provider import SecShadowingCatalystProvider
from app.configuration import load_configuration
from app.momentum_scanner.models import CatalystStatus, CatalystType


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def _evidence(symbol="ABC", status=CatalystStatus.FALSE):
    return CatalystEvidence(
        symbol=symbol,
        catalyst_type=CatalystType.NONE,
        status=status,
        source="SEC_EDGAR",
    )


class Legacy:
    name = "SEC_EDGAR"

    def __init__(self, result=None, error=None):
        self.result = result or _evidence()
        self.error = error
        self.calls = []

    def get_evidence(self, symbol, as_of=None):
        self.calls.append((symbol, as_of))
        if self.error:
            raise self.error
        return self.result


class Evaluator:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def evaluate(self, symbol, *, as_of, legacy_evidence):
        self.calls.append((symbol, as_of, legacy_evidence))
        if self.error:
            raise self.error


def test_wrapper_delegates_name_calls_once_and_returns_exact_legacy_object():
    legacy_result = _evidence()
    legacy = Legacy(legacy_result)
    evaluator = Evaluator()
    wrapper = SecShadowingCatalystProvider(legacy, evaluator, clock=lambda: NOW)

    result = wrapper.get_evidence("ABC")

    assert wrapper.name == legacy.name
    assert result is legacy_result
    assert legacy.calls == [("ABC", NOW)]
    assert evaluator.calls == [("ABC", NOW, legacy_result)]


def test_wrapper_passes_same_explicit_as_of_and_symbol():
    legacy = Legacy()
    evaluator = Evaluator()
    wrapper = SecShadowingCatalystProvider(legacy, evaluator, clock=lambda: NOW)
    cutoff = datetime(2026, 9, 15, 10, tzinfo=UTC)

    wrapper.get_evidence("ABC", cutoff)

    assert legacy.calls == [("ABC", cutoff)]
    assert evaluator.calls[0][0:2] == ("ABC", cutoff)


def test_wrapper_shadow_failure_is_non_fatal_and_legacy_failure_is_preserved():
    legacy_result = _evidence()
    legacy = Legacy(legacy_result)
    wrapper = SecShadowingCatalystProvider(
        legacy, Evaluator(RuntimeError("private")), clock=lambda: NOW,
    )
    assert wrapper.get_evidence("ABC") is legacy_result

    failing = Legacy(error=RuntimeError("legacy failure"))
    failing_wrapper = SecShadowingCatalystProvider(failing, Evaluator(), clock=lambda: NOW)
    with pytest.raises(RuntimeError, match="legacy failure"):
        failing_wrapper.get_evidence("ABC")
    assert failing_wrapper._shadow_evaluator.calls == []


def test_composition_wraps_only_sec_and_preserves_order_when_evaluator_supplied():
    configuration = load_configuration({"SEC_EDGAR_USER_AGENT": "test@example.invalid"})
    evaluator = Evaluator()
    providers = build_catalyst_providers(SimpleNamespace(), configuration, sec_shadow_evaluator=evaluator)
    assert providers[0].name == "WEBULL_EARNINGS_SEC"
    assert isinstance(providers[1], SecShadowingCatalystProvider)
    assert tuple(item.name for item in providers) == (
        "WEBULL_EARNINGS_SEC", "SEC_EDGAR",
    )


def test_composition_without_evaluator_keeps_raw_legacy_provider():
    configuration = load_configuration({"SEC_EDGAR_USER_AGENT": "test@example.invalid"})
    providers = build_catalyst_providers(SimpleNamespace(), configuration)
    assert type(providers[1]).__name__ == "SECEdgarCatalystProvider"


def test_composition_without_legacy_sec_does_not_wrap():
    configuration = load_configuration({})
    evaluator = Evaluator()
    providers = build_catalyst_providers(SimpleNamespace(), configuration, sec_shadow_evaluator=evaluator)
    assert tuple(item.name for item in providers) == ("WEBULL_EARNINGS_SEC",)
    assert evaluator.calls == []
