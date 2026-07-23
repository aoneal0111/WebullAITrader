from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from app.evidence import EvidenceProvider, SignalDirection
from app.evidence.exceptions import EvidenceValidationError
from app.evidence.providers import (
    TechnicalSnapshotEvidenceProvider,
    TechnicalSnapshotInput,
)
from app.indicators.market_snapshot import MarketSnapshot


NOW = datetime(2026, 7, 21, 15, 30, tzinfo=UTC)


def snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "symbol": " aapl ",
        "close": Decimal("100"),
        "ema_12": Decimal("101"),
        "ema_26": Decimal("99"),
        "rsi_14": Decimal("25"),
        "macd": Decimal("1.2"),
        "macd_signal": Decimal("1.0"),
        "macd_histogram": Decimal("0.2"),
        "atr_14": Decimal("2"),
        "bollinger_upper": Decimal("110"),
        "bollinger_middle": Decimal("100"),
        "bollinger_lower": Decimal("90"),
        "vwap": Decimal("98"),
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def generate(**overrides: object):
    return TechnicalSnapshotEvidenceProvider().generate(
        TechnicalSnapshotInput(snapshot(**overrides), NOW)
    )


def indicator(items, name: str):
    return next(item for item in items if item.metadata["indicator"] == name)


def test_provider_protocol_name_and_full_snapshot() -> None:
    provider = TechnicalSnapshotEvidenceProvider()
    assert isinstance(provider, EvidenceProvider)
    assert provider.name == "technical_snapshot_v1"
    items = provider.generate(TechnicalSnapshotInput(snapshot(), NOW))
    assert len(items) == 6
    assert all(item.source == provider.name for item in items)
    assert all(item.symbol == "AAPL" for item in items)
    assert all(item.timestamp == NOW for item in items)
    assert all(item.metadata["provider_version"] == "1" for item in items)
    assert all(item.metadata["deterministic"] is True for item in items)


@pytest.mark.parametrize(
    ("ema_12", "ema_26", "expected"),
    [("101", "99", SignalDirection.LONG), ("99", "101", SignalDirection.SHORT), ("100", "100", SignalDirection.NEUTRAL)],
)
def test_ema_directions(ema_12: str, ema_26: str, expected: SignalDirection) -> None:
    assert indicator(generate(ema_12=Decimal(ema_12), ema_26=Decimal(ema_26)), "ema_cross").direction is expected


@pytest.mark.parametrize(
    ("histogram", "expected"),
    [("0.2", SignalDirection.LONG), ("-0.2", SignalDirection.SHORT), ("0", SignalDirection.NEUTRAL)],
)
def test_macd_directions(histogram: str, expected: SignalDirection) -> None:
    assert indicator(generate(macd_histogram=Decimal(histogram)), "macd").direction is expected


@pytest.mark.parametrize(
    ("rsi", "expected", "condition"),
    [("25", SignalDirection.LONG, "oversold"), ("75", SignalDirection.SHORT, "overbought"), ("50", SignalDirection.NEUTRAL, "midrange")],
)
def test_rsi_conditions(rsi: str, expected: SignalDirection, condition: str) -> None:
    item = indicator(generate(rsi_14=Decimal(rsi)), "rsi_14")
    assert item.direction is expected
    assert condition in item.explanation.lower()


def test_missing_rsi_still_emits_required_signals() -> None:
    items = generate(rsi_14=None, vwap=None, atr_14=None, bollinger_upper=None, bollinger_middle=None, bollinger_lower=None)
    assert tuple(item.metadata["indicator"] for item in items) == ("ema_cross", "macd")


@pytest.mark.parametrize(
    ("vwap", "expected"),
    [("99", SignalDirection.LONG), ("101", SignalDirection.SHORT), ("100", SignalDirection.NEUTRAL)],
)
def test_vwap_directions(vwap: str, expected: SignalDirection) -> None:
    assert indicator(generate(vwap=Decimal(vwap)), "vwap").direction is expected


def test_missing_vwap_is_omitted() -> None:
    assert all(item.metadata["indicator"] != "vwap" for item in generate(vwap=None))


@pytest.mark.parametrize(
    ("close", "expected"),
    [("89", SignalDirection.LONG), ("90", SignalDirection.LONG), ("100", SignalDirection.NEUTRAL), ("110", SignalDirection.SHORT), ("111", SignalDirection.SHORT)],
)
def test_bollinger_locations(close: str, expected: SignalDirection) -> None:
    assert indicator(generate(close=Decimal(close)), "bollinger_bands").direction is expected


def test_partial_bollinger_data_is_omitted() -> None:
    assert all(item.metadata["indicator"] != "bollinger_bands" for item in generate(bollinger_middle=None))


def test_zero_width_bollinger_is_safe_neutral() -> None:
    item = indicator(generate(bollinger_lower=Decimal("100"), bollinger_middle=Decimal("100"), bollinger_upper=Decimal("100")), "bollinger_bands")
    assert item.direction is SignalDirection.NEUTRAL
    assert item.strength == 0
    assert item.confidence == 0.5


def test_invalid_bollinger_ordering_is_rejected() -> None:
    with pytest.raises(EvidenceValidationError, match="lower <= middle <= upper"):
        generate(bollinger_lower=Decimal("101"))


def test_atr_is_always_neutral_and_has_context_role() -> None:
    item = indicator(generate(atr_14=Decimal("8")), "atr_14")
    assert item.direction is SignalDirection.NEUTRAL
    assert item.metadata["role"] == "volatility_context"
    assert "high volatility" in item.explanation


def test_missing_atr_is_omitted() -> None:
    assert all(item.metadata["indicator"] != "atr_14" for item in generate(atr_14=None))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"atr_14": Decimal("-1")}, "cannot be negative"),
        ({"rsi_14": Decimal("101")}, "between 0 and 100"),
        ({"close": Decimal("0")}, "positive"),
        ({"close": Decimal("-1")}, "positive"),
        ({"macd": Decimal("NaN")}, "finite"),
        ({"vwap": Decimal("Infinity")}, "finite"),
        ({"symbol": " "}, "symbol cannot be empty"),
    ],
)
def test_invalid_snapshot_values(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(EvidenceValidationError, match=message):
        generate(**overrides)


def test_naive_timestamp_and_wrong_input_are_rejected() -> None:
    provider = TechnicalSnapshotEvidenceProvider()
    with pytest.raises(EvidenceValidationError, match="timezone-aware"):
        provider.generate(TechnicalSnapshotInput(snapshot(), datetime(2026, 7, 21)))
    with pytest.raises(EvidenceValidationError, match="TechnicalSnapshotInput"):
        provider.generate(snapshot())  # type: ignore[arg-type]


def test_feature_and_metadata_mappings_are_immutable() -> None:
    item = generate()[0]
    assert isinstance(item.features, MappingProxyType)
    assert isinstance(item.metadata, MappingProxyType)
    with pytest.raises(TypeError):
        item.features["close"] = "1"  # type: ignore[index]
    with pytest.raises(TypeError):
        item.metadata["indicator"] = "other"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        item.source = "other"  # type: ignore[misc]


def test_identical_inputs_are_semantically_identical_except_unique_ids() -> None:
    first = generate()
    second = generate()
    assert [item.evidence_id for item in first] != [item.evidence_id for item in second]
    for left, right in zip(first, second, strict=True):
        left_dict, right_dict = left.to_dict(), right.to_dict()
        left_dict.pop("evidence_id")
        right_dict.pop("evidence_id")
        assert left_dict == right_dict


def test_generation_uses_supplied_data_without_clock_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider module has no clock or network dependency to patch or invoke.
    items = generate()
    assert items and all(item.timestamp == NOW for item in items)
