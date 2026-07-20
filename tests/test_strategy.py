from app.indicators.market_snapshot import MarketSnapshot
from app.strategy.scoring import StrategyAction, analyze_snapshot, score_snapshot
from app.strategy.volatility import VolatilityRegime


def _snapshot(**overrides: float | None | str) -> MarketSnapshot:
    values: dict[str, object] = {
        "symbol": "TEST",
        "close": 110.0,
        "ema_12": 105.0,
        "ema_26": 100.0,
        "rsi_14": 60.0,
        "macd": 2.0,
        "macd_signal": 1.0,
        "macd_histogram": 1.0,
        "atr_14": 2.0,
        "bollinger_upper": 115.0,
        "bollinger_middle": 105.0,
        "bollinger_lower": 95.0,
        "vwap": 104.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def test_bullish_snapshot_scores_buy() -> None:
    result = score_snapshot(_snapshot())
    assert result.action is StrategyAction.BUY
    assert 0 <= result.confidence <= 100
    assert result.volatility_regime is VolatilityRegime.NORMAL


def test_bearish_snapshot_scores_sell() -> None:
    result = score_snapshot(
        _snapshot(close=90.0, ema_12=95.0, ema_26=100.0, rsi_14=40.0, macd=-2.0, macd_signal=-1.0, macd_histogram=-1.0)
    )
    assert result.action is StrategyAction.SELL


def test_missing_indicators_degrade_safely() -> None:
    result = score_snapshot(_snapshot(rsi_14=None, atr_14=None))
    assert result.volatility_regime is VolatilityRegime.UNKNOWN
    assert len(result.reasons) == 3


def test_analysis_has_requested_json_shape() -> None:
    analysis = analyze_snapshot(
        _snapshot(
            close=94.0,
            ema_12=101.0,
            ema_26=100.0,
            rsi_14=25.0,
            macd=0.5,
            macd_signal=1.0,
            macd_histogram=-0.5,
            atr_14=4.0,
            bollinger_lower=95.0,
        )
    ).to_dict()
    assert set(analysis) == {
        "trend",
        "momentum",
        "volatility",
        "ema_cross",
        "macd_cross",
        "rsi_state",
        "bollinger_state",
        "overall_score",
    }
    assert analysis["momentum"] == "Weakening"
    assert analysis["ema_cross"] is True
    assert analysis["macd_cross"] is False
    assert analysis["rsi_state"] == "Oversold"
    assert analysis["bollinger_state"] == "Lower Band Touch"
