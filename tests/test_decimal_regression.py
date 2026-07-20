from decimal import Decimal
from dataclasses import fields

import pytest

from app.indicators.market_snapshot import MarketSnapshot
from app.strategy.scoring import StrategyAction, analyze_snapshot, score_snapshot


CASES = (
    (
        StrategyAction.BUY,
        {"close": "110", "ema_12": "105", "ema_26": "100", "rsi_14": "60", "macd": "2", "macd_signal": "1", "macd_histogram": "1"},
    ),
    (
        StrategyAction.SELL,
        {"close": "90", "ema_12": "95", "ema_26": "100", "rsi_14": "40", "macd": "-2", "macd_signal": "-1", "macd_histogram": "-1"},
    ),
    (
        StrategyAction.HOLD,
        {"close": "100", "ema_12": "100", "ema_26": "100", "rsi_14": "50", "macd": "0", "macd_signal": "0", "macd_histogram": "0"},
    ),
)


def _snapshot(values: dict[str, str], *, decimal_input: bool) -> MarketSnapshot:
    convert = Decimal if decimal_input else float
    converted = {key: convert(value) for key, value in values.items()}
    return MarketSnapshot(
        symbol="TEST",
        **converted,
        atr_14=convert("2"),
        bollinger_upper=convert("115"),
        bollinger_middle=convert("100"),
        bollinger_lower=convert("85"),
        vwap=convert("100"),
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("expected, values", CASES)
def test_decimal_migration_preserves_strategy_decisions(
    expected: StrategyAction, values: dict[str, str]
) -> None:
    legacy_compatible = _snapshot(values, decimal_input=False)
    decimal_native = _snapshot(values, decimal_input=True)
    legacy_score = score_snapshot(legacy_compatible)
    decimal_score = score_snapshot(decimal_native)
    assert legacy_score == decimal_score
    assert legacy_score.action is expected
    assert analyze_snapshot(legacy_compatible) == analyze_snapshot(decimal_native)


def test_snapshot_numeric_fields_are_decimal_after_compatibility_conversion() -> None:
    snapshot = _snapshot(CASES[0][1], decimal_input=False)
    assert all(
        value is None or isinstance(value, Decimal)
        for field in fields(snapshot)
        if field.name != "symbol"
        for value in (getattr(snapshot, field.name),)
    )
