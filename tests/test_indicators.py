import pytest

from app.indicators.atr import atr
from app.indicators.bollinger import bollinger_bands
from app.indicators.ema import ema
from app.indicators.macd import macd
from app.indicators.market_snapshot import build_market_snapshot
from app.indicators.rsi import rsi
from app.indicators.vwap import vwap


def test_indicator_outputs_align_with_input() -> None:
    closes = [float(value) for value in range(1, 31)]
    highs = [value + 1 for value in closes]
    lows = [value - 1 for value in closes]
    volumes = [100.0] * len(closes)
    assert len(ema(closes, 12)) == len(closes)
    assert len(rsi(closes)) == len(closes)
    assert len(macd(closes).histogram) == len(closes)
    assert len(atr(highs, lows, closes)) == len(closes)
    assert len(bollinger_bands(closes).middle) == len(closes)
    assert len(vwap(highs, lows, closes, volumes)) == len(closes)


def test_market_snapshot_builds_latest_values() -> None:
    closes = [100.0 + index for index in range(30)]
    snapshot = build_market_snapshot(
        "test",
        [value + 1 for value in closes],
        [value - 1 for value in closes],
        closes,
        [1000.0] * len(closes),
    )
    assert snapshot.symbol == "TEST"
    assert snapshot.close == 129.0
    assert snapshot.rsi_14 == pytest.approx(100.0)
    assert snapshot.atr_14 is not None


def test_invalid_market_data_is_rejected() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        build_market_snapshot("TEST", [1.0], [0.5], [0.8], [])
