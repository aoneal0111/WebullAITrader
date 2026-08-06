"""Deterministic chart geometry and axis formatting independent of Qt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import floor, log10
from zoneinfo import ZoneInfo

from app.gui.models.chart import ChartCandle


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class AxisTick:
    position: float
    label: str
    value: Decimal | datetime
    candle_index: int | None = None


@dataclass(frozen=True, slots=True)
class ChartGeometry:
    candles: tuple[ChartCandle, ...]
    source_offset: int
    plot_left: float
    plot_top: float
    plot_right: float
    plot_bottom: float
    candle_centers: tuple[float, ...]
    candle_width: float
    visible_min: Decimal | None
    visible_max: Decimal | None
    price_min: Decimal | None
    price_max: Decimal | None
    price_ticks: tuple[AxisTick, ...]
    time_ticks: tuple[AxisTick, ...]

    def y_for_price(self, value: Decimal) -> float:
        if self.price_min is None or self.price_max is None:
            return self.plot_bottom
        spread = self.price_max - self.price_min
        if spread == 0:
            return (self.plot_top + self.plot_bottom) / 2
        return self.plot_top + float((self.price_max - value) / spread) * (
            self.plot_bottom - self.plot_top
        )

    def price_for_y(self, y: float) -> Decimal | None:
        if self.price_min is None or self.price_max is None:
            return None
        height = max(1.0, self.plot_bottom - self.plot_top)
        ratio = Decimal(str((min(max(y, self.plot_top), self.plot_bottom) - self.plot_top) / height))
        return self.price_max - ratio * (self.price_max - self.price_min)

    def nearest_candle(self, x: float) -> int | None:
        if not self.candle_centers or x < self.plot_left or x > self.plot_right:
            return None
        local = min(range(len(self.candle_centers)), key=lambda i: abs(self.candle_centers[i] - x))
        return self.source_offset + local


def calculate_chart_geometry(
    candles: tuple[ChartCandle, ...],
    width: int,
    height: int,
    *,
    display_timezone=NEW_YORK,
    maximum_bars: int = 120,
) -> ChartGeometry:
    visible = tuple(candles[-maximum_bars:])
    offset = len(candles) - len(visible)
    left, top = 10.0, 12.0
    right = max(left + 1.0, float(width) - 76.0)
    bottom = max(top + 1.0, float(height) - 36.0)
    plot_width = max(1.0, right - left)
    step = plot_width / max(1, len(visible) + 0.65)
    centers = tuple(left + (index + 0.5) * step for index in range(len(visible)))
    body_width = max(1.0, min(10.0, step * 0.62))
    if not visible:
        return ChartGeometry((), offset, left, top, right, bottom, (), body_width,
                             None, None, None, None, (), ())

    visible_min = min(candle.low for candle in visible)
    visible_max = max(candle.high for candle in visible)
    raw_spread = visible_max - visible_min
    reference = max(abs(visible_min), abs(visible_max), Decimal("1"))
    padding = max(raw_spread * Decimal("0.08"), reference * Decimal("0.0025"))
    price_min = visible_min - padding
    price_max = visible_max + padding
    price_ticks = _price_ticks(price_min, price_max, top, bottom)
    time_ticks = _time_ticks(visible, centers, display_timezone, plot_width)
    return ChartGeometry(
        visible, offset, left, top, right, bottom, centers, body_width,
        visible_min, visible_max, price_min, price_max, price_ticks, time_ticks,
    )


def _price_ticks(low: Decimal, high: Decimal, top: float, bottom: float) -> tuple[AxisTick, ...]:
    desired = max(2, min(8, int((bottom - top) // 48) + 1))
    step = _nice_step((high - low) / max(1, desired - 1))
    first = (low / step).to_integral_value(rounding=ROUND_CEILING) * step
    last = (high / step).to_integral_value(rounding=ROUND_FLOOR) * step
    values: list[Decimal] = []
    value = first
    while value <= last and len(values) < 20:
        values.append(value)
        value += step
    if len(values) < 2:
        values = [low, high]
    precision = max(0, min(8, -step.normalize().as_tuple().exponent))
    spread = high - low
    return tuple(
        AxisTick(
            top + float((high - value) / spread) * (bottom - top),
            f"{value:,.{precision}f}",
            value,
        )
        for value in values
    )


def _nice_step(value: Decimal) -> Decimal:
    numeric = max(float(value), 1e-12)
    magnitude = 10 ** floor(log10(numeric))
    fraction = numeric / magnitude
    nice = 1 if fraction <= 1 else 2 if fraction <= 2 else 2.5 if fraction <= 2.5 else 5 if fraction <= 5 else 10
    return Decimal(str(nice * magnitude))


def _time_ticks(
    candles: tuple[ChartCandle, ...],
    centers: tuple[float, ...],
    display_timezone,
    plot_width: float,
) -> tuple[AxisTick, ...]:
    maximum = max(1, min(len(candles), int(plot_width // 78)))
    if maximum == 1:
        indices = (len(candles) - 1,)
    else:
        indices = tuple(dict.fromkeys(
            round(index * (len(candles) - 1) / (maximum - 1))
            for index in range(maximum)
        ))
    local = tuple(candle.timestamp.astimezone(display_timezone) for candle in candles)
    multiple_dates = len({item.date() for item in local}) > 1
    intraday = len(local) < 2 or (local[-1] - local[0]).total_seconds() < 3 * 86400
    ticks = []
    previous_date = None
    for index in indices:
        stamp = local[index]
        if intraday:
            label = stamp.strftime("%b %d %H:%M") if multiple_dates and stamp.date() != previous_date else stamp.strftime("%H:%M")
        else:
            label = stamp.strftime("%b %d")
            if stamp.year != local[0].year:
                label = stamp.strftime("%b %d %Y")
        ticks.append(AxisTick(centers[index], label, candles[index].timestamp, index))
        previous_date = stamp.date()
    return tuple(ticks)


__all__ = ["AxisTick", "ChartGeometry", "NEW_YORK", "calculate_chart_geometry"]
