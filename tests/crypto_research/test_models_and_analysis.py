from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.assets import AssetType
from app.crypto_research.analysis import calculate_features, detect_events, score_features
from app.crypto_research import (
    CryptoMarketSession,
    CryptoMomentumEventType,
    CryptoObservation,
    CryptoPair,
    CryptoResearchDecision,
    CryptoResearchRegime,
    crypto_regime,
)


T0 = datetime(2026, 9, 7, 12, tzinfo=UTC)


def observation(index: int, price: str, volume: str | None) -> CryptoObservation:
    value = Decimal(price)
    return CryptoObservation(
        CryptoPair("btc", "usd", "XBT-USD"),
        T0 + timedelta(minutes=index),
        value,
        value - Decimal("0.1"),
        value + Decimal("0.1"),
        None if volume is None else Decimal(volume),
        high=value + Decimal("0.2"),
        low=value - Decimal("0.2"),
    )


def test_pair_identity_is_explicit_immutable_and_collision_safe() -> None:
    pair = CryptoPair.configured("btc/usd")
    assert pair.canonical_symbol == "BTC/USD"
    assert pair.provider_symbol == "BTCUSD"
    assert pair.identity == (AssetType.CRYPTO, "BTC/USD")
    assert pair.identity != (AssetType.EQUITY, "BTC")
    assert CryptoPair.configured("eth-usd").canonical_symbol == "ETH/USD"
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        CryptoPair.configured("BTCUSD")


def test_crypto_session_is_continuous_and_regimes_are_analytic_only() -> None:
    assert CryptoMarketSession.CONTINUOUS_24_7.value == "CONTINUOUS_24_7"
    assert crypto_regime(datetime(2026, 9, 5, 15, tzinfo=UTC)) is CryptoResearchRegime.WEEKEND
    assert crypto_regime(datetime(2026, 9, 7, 2, tzinfo=UTC)) is CryptoResearchRegime.ASIA
    assert crypto_regime(datetime(2026, 9, 7, 9, tzinfo=UTC)) is CryptoResearchRegime.EUROPE
    assert crypto_regime(datetime(2026, 9, 7, 15, tzinfo=UTC)) is CryptoResearchRegime.US


def test_features_events_and_score_are_point_in_time_and_transparent() -> None:
    history = (
        observation(0, "100", "100"),
        observation(1, "101", "110"),
        observation(2, "103", "220"),
    )
    features = calculate_features(history, cutoff=T0 + timedelta(minutes=2, seconds=1))
    events = detect_events(history, features)
    score, components = score_features(features, events)
    assert features.percentage_change == Decimal("3")
    assert features.notional_volume == Decimal("22660")
    assert features.volume_acceleration == Decimal("11")
    assert features.asset_type is AssetType.CRYPTO
    assert features.relative_volume_time_of_week is None
    assert features.float_shares is None and features.premarket_gap is None
    assert CryptoMomentumEventType.RANGE_BREAKOUT in events
    assert CryptoMomentumEventType.MOMENTUM_ACCELERATION in events
    assert CryptoMomentumEventType.VOLUME_EXPANSION in events
    assert Decimal("0") <= score <= Decimal("100")
    assert dict(components).keys() == {
        "price_acceleration", "volume_acceleration", "liquidity",
        "spread_quality", "breakout_structure", "trend_persistence",
        "event_structure",
    }


def test_serialization_preserves_authority_denials_and_pair_identity() -> None:
    item = observation(0, "100", "10")
    features = calculate_features((item,), cutoff=T0)
    score, components = score_features(features, ())
    decision = CryptoResearchDecision(
        "1", item.pair, T0, T0, item.price, item.bid, item.ask,
        features.spread, item.volume, features, (), score, components, 1,
        CryptoResearchRegime.WEEKEND,
    )
    record = decision.to_record()
    assert record["asset_type"] == "CRYPTO"
    assert record["canonical_symbol"] == "BTC/USD"
    assert record["provider_symbol"] == "XBT-USD"
    assert record["session"] == "CONTINUOUS_24_7"
    assert record["research_only"] is True
    assert record["production_promoted"] is False
    assert record["selection_authorized"] is False
    assert record["execution_authorized"] is False


def test_missing_volume_is_unavailable_through_features_score_and_serialization() -> None:
    history = (
        observation(0, "100", None),
        observation(1, "101", None),
        observation(2, "103", None),
    )
    features = calculate_features(history, cutoff=T0 + timedelta(minutes=2))
    events = detect_events(history, features)
    score, components = score_features(features, events)

    assert features.volume is None
    assert features.notional_volume is None
    assert features.volume_acceleration is None
    assert CryptoMomentumEventType.VOLUME_EXPANSION not in events
    assert dict(components)["volume_acceleration"] is None
    assert dict(components)["liquidity"] is None
    assert Decimal("0") <= score <= Decimal("100")
    assert dict(components)["price_acceleration"] is not None

    item = history[-1]
    decision = CryptoResearchDecision(
        "1", item.pair, item.timestamp, item.timestamp, item.price, item.bid,
        item.ask, features.spread, item.volume, features, events, score,
        components, 1, CryptoResearchRegime.WEEKEND,
    )
    record = decision.to_record()
    assert record["volume"] is None
    assert record["features"]["volume"] is None
    assert record["features"]["notional_volume"] is None
    assert record["score_components"]["volume_acceleration"] is None
    assert record["score_components"]["liquidity"] is None
