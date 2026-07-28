from __future__ import annotations

from decimal import Decimal
from threading import RLock

from .models import ReplaySpeed


class ReplayClock:
    """Logical replay clock advanced explicitly by callers."""

    def __init__(
        self,
        speed: ReplaySpeed = ReplaySpeed.X1,
    ) -> None:
        if not isinstance(speed, ReplaySpeed):
            raise TypeError("speed must be a ReplaySpeed")
        self._lock = RLock()
        self._speed = ReplaySpeed.PAUSED
        self._resume_speed = (
            ReplaySpeed.X1
            if speed is ReplaySpeed.PAUSED
            else speed
        )
        self._elapsed = Decimal("0")

    @property
    def speed(self) -> ReplaySpeed:
        with self._lock:
            return self._speed

    @property
    def elapsed(self) -> Decimal:
        with self._lock:
            return self._elapsed

    def set_speed(self, speed: ReplaySpeed) -> None:
        if not isinstance(speed, ReplaySpeed):
            raise TypeError("speed must be a ReplaySpeed")
        with self._lock:
            if speed is ReplaySpeed.PAUSED:
                self._speed = ReplaySpeed.PAUSED
            else:
                self._resume_speed = speed
                self._speed = speed

    def pause(self) -> None:
        with self._lock:
            self._speed = ReplaySpeed.PAUSED

    def resume(self) -> None:
        with self._lock:
            self._speed = self._resume_speed

    def reset(self) -> None:
        with self._lock:
            self._elapsed = Decimal("0")
            self._speed = ReplaySpeed.PAUSED

    def seek(self, elapsed_seconds: Decimal) -> None:
        value = _validate_duration(elapsed_seconds)
        with self._lock:
            self._elapsed = value

    def advance(self, elapsed_seconds: Decimal) -> Decimal:
        value = _validate_duration(elapsed_seconds)
        with self._lock:
            self._elapsed += value * self._speed.multiplier
            return self._elapsed


def _validate_duration(value: Decimal) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
    ):
        raise ValueError(
            "elapsed_seconds must be a finite nonnegative Decimal"
        )
    return value
