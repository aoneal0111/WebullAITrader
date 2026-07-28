from decimal import Decimal

import pytest

from app.replay import ReplayClock, ReplaySpeed


def test_clock_advances_only_by_configured_logical_speed() -> None:
    clock = ReplayClock(ReplaySpeed.X2)

    assert clock.advance(Decimal("5")) == Decimal("0")
    clock.resume()
    assert clock.advance(Decimal("1.5")) == Decimal("3.0")
    clock.set_speed(ReplaySpeed.X5)
    assert clock.advance(Decimal("1")) == Decimal("8.0")
    clock.pause()
    assert clock.advance(Decimal("10")) == Decimal("8.0")


def test_clock_seek_reset_and_speed_changes_are_deterministic() -> None:
    clock = ReplayClock()
    clock.seek(Decimal("12.5"))
    clock.set_speed(ReplaySpeed.X10)

    assert clock.elapsed == Decimal("12.5")
    assert clock.speed is ReplaySpeed.X10

    clock.reset()
    assert clock.elapsed == Decimal("0")
    assert clock.speed is ReplaySpeed.PAUSED


@pytest.mark.parametrize(
    "value",
    (
        Decimal("-1"),
        Decimal("NaN"),
        1,
    ),
)
def test_clock_rejects_invalid_durations(value) -> None:
    clock = ReplayClock()

    with pytest.raises(ValueError, match="finite nonnegative"):
        clock.advance(value)
