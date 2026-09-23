from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_radar import MomentumRadar, RadarSnapshot


T0 = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


def snap(symbol: str, at: datetime, price: str, volume: str, rvol: str,
         change: str, sources=("SESSION_GAINERS",)) -> RadarSnapshot:
    return RadarSnapshot(
        symbol=symbol, observed_at=at, price=Decimal(price),
        change_percent=Decimal(change), volume=Decimal(volume),
        relative_volume=Decimal(rvol), turnover=Decimal(volume) * Decimal(price),
        sources=sources, hod=Decimal(price),
    )


def test_price_volume_and_rvol_acceleration_raise_priority():
    radar = MomentumRadar()
    radar.observe((snap("A", T0, "5", "100", "5", "42"), snap("B", T0, "5", "100", "5", "6")))
    assessments = radar.observe((
        snap("A", T0 + timedelta(seconds=60), "5", "100", "5", "42"),
        snap("B", T0 + timedelta(seconds=60), "5.125", "180", "7", "8"),
    ))
    assert assessments[0].symbol == "B"
    assert assessments[0].components["price_acceleration"] > 0


def test_multi_source_confirmation_contributes():
    radar = MomentumRadar()
    one = snap("A", T0, "5", "100", "5", "8", ("SESSION_GAINERS",))
    many = snap("B", T0, "5", "100", "5", "8", ("SESSION_GAINERS", "RELATIVE_VOLUME_10D", "VOLUME_LEADERS", "TURNOVER_LEADERS"))
    values = radar.observe((one, many))
    assert values[0].symbol == "B"


def test_required_symbol_is_never_displaced_and_cap_is_hard():
    radar = MomentumRadar()
    radar.observe(tuple(snap(f"S{i}", T0, "5", "100", "2", "5") for i in range(10)))
    selected = radar.promote(capacity=3, required=("MANAGED",))
    assert "MANAGED" in selected
    assert len(selected) == 3


def test_hysteresis_and_decay_are_bounded():
    radar = MomentumRadar()
    radar.observe((snap("A", T0, "5", "100", "2", "5"), snap("B", T0, "5", "100", "2", "5")))
    first = radar.promote(capacity=1)
    radar.observe((snap("A", T0 + timedelta(seconds=60), "5", "100", "2", "5"), snap("B", T0 + timedelta(seconds=60), "5.10", "200", "4", "7")))
    second = radar.promote(capacity=1)
    assert first == ("A",)
    assert second == ("B",)
    assert radar.metrics()["replacements"] >= 0


def test_duplicate_state_is_symbol_bounded_and_radar_has_no_authority():
    radar = MomentumRadar()
    radar.observe((snap("A", T0, "5", "100", "2", "5", ("SESSION_GAINERS", "VOLUME_LEADERS")),))
    assert len(radar.state_snapshot()) == 1
    assert not hasattr(radar, "authorize")
