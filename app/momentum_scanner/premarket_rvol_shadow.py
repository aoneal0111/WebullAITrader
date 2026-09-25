"""Research-only, time-aligned premarket relative-volume comparison.

This module deliberately has no dependency on scanner qualification, Warrior,
risk, or order submission.  Callers must explicitly preload completed
historical minute bars and provide the current completed premarket cumulative
volume.  A result is descriptive evidence only.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from threading import RLock

from app.market.calendar import EASTERN


ZERO = Decimal("0")
PREMARKET_START = time(4, 0)
REGULAR_SESSION_START = time(9, 30)
PREMARKET_MINUTES = 330
DEFAULT_MINIMUM_HISTORICAL_SESSIONS = 10
PREMARKET_PROFILE_VERSION = "PREMARKET_M1_CUMULATIVE_V1"


class PremarketRvolShadowStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_PREMARKET = "NOT_PREMARKET"


class PremarketVolumeProfileStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    EXTENDED_HOURS_HISTORY_UNAVAILABLE = "EXTENDED_HOURS_HISTORY_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PremarketRvolShadowResult:
    symbol: str
    comparison_timestamp: datetime
    session: str
    legacy_rvol: Decimal | None
    premarket_normalized_rvol: Decimal | None
    historical_sample_count: int
    status: PremarketRvolShadowStatus
    legacy_pass: bool | None
    shadow_pass: bool | None
    profile_status: str = PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
    # This invariant makes the research boundary explicit to consumers.
    authoritative: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class PremarketVolumeProfile:
    symbol: str
    profile_version: str
    historical_cutoff: date
    expected_cumulative: tuple[Decimal, ...]
    session_count: int
    status: PremarketVolumeProfileStatus


class PremarketRvolShadow:
    """Bounded cache of historical expected-volume curves.

    ``prepare`` performs the historical scan once.  ``lookup`` is O(1) and
    does not inspect historical bars, making it safe for observational use on
    a quote/trade path if a caller later elects to compose it.
    """

    def __init__(
        self,
        *,
        minimum_historical_sessions: int = DEFAULT_MINIMUM_HISTORICAL_SESSIONS,
        maximum_curves: int = 512,
        telemetry_sink: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if minimum_historical_sessions <= 0:
            raise ValueError("minimum_historical_sessions must be positive")
        if maximum_curves <= 0:
            raise ValueError("maximum_curves must be positive")
        if telemetry_sink is not None and not callable(telemetry_sink):
            raise TypeError("telemetry_sink must be callable or None")
        self.minimum_historical_sessions = minimum_historical_sessions
        self.maximum_curves = maximum_curves
        self._telemetry_sink = telemetry_sink
        self._curves: OrderedDict[
            tuple[str, date, str], PremarketVolumeProfile
        ] = OrderedDict()
        self._lock = RLock()

    def prepare(
        self,
        symbol: str,
        historical_bars: Iterable[object],
        *,
        as_of: datetime,
    ) -> int:
        """Precompute a curve using only sessions strictly before ``as_of``.

        Duplicate timestamps collapse to one completed bar.  Regular-session,
        postmarket, overnight, current-day, and future bars are excluded.
        """
        profile = self.build_profile(symbol, historical_bars, as_of=as_of)
        self.install_profile(profile)
        return profile.session_count

    def build_profile(
        self,
        symbol: str,
        historical_bars: Iterable[object],
        *,
        as_of: datetime,
        profile_version: str = PREMARKET_PROFILE_VERSION,
    ) -> PremarketVolumeProfile:
        """Build an immutable curve without publishing partial state."""
        normalized = _symbol(symbol)
        local_as_of = _aware(as_of).astimezone(EASTERN)
        comparison_date = local_as_of.date()
        unique: dict[datetime, Decimal] = {}
        for value in historical_bars:
            timestamp = getattr(value, "timestamp", None)
            volume = getattr(value, "volume", None)
            if timestamp is None or volume is None:
                continue
            try:
                aware_timestamp = _aware(timestamp)
                amount = Decimal(str(volume))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if not amount.is_finite() or amount < ZERO:
                continue
            local = aware_timestamp.astimezone(EASTERN)
            if local.date() >= comparison_date or not _is_premarket(local):
                continue
            unique[aware_timestamp] = amount

        sessions: dict[date, list[Decimal]] = {}
        for timestamp, amount in sorted(unique.items()):
            local = timestamp.astimezone(EASTERN)
            values = sessions.setdefault(
                local.date(), [ZERO for _ in range(PREMARKET_MINUTES)],
            )
            values[_premarket_minute(local)] += amount

        curves: list[tuple[Decimal, ...]] = []
        for values in sessions.values():
            running = ZERO
            cumulative: list[Decimal] = []
            for amount in values:
                running += amount
                cumulative.append(running)
            curves.append(tuple(cumulative))

        sample_count = len(curves)
        if sample_count < self.minimum_historical_sessions:
            expected = ()
            status = PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
        else:
            divisor = Decimal(sample_count)
            expected = tuple(
                sum((curve[index] for curve in curves), ZERO) / divisor
                for index in range(PREMARKET_MINUTES)
            )
            status = PremarketVolumeProfileStatus.AVAILABLE
        return PremarketVolumeProfile(
            normalized, str(profile_version), comparison_date,
            expected, sample_count, status,
        )

    def install_profile(self, profile: PremarketVolumeProfile) -> None:
        """Atomically publish one completed immutable profile."""
        if not isinstance(profile, PremarketVolumeProfile):
            raise TypeError("profile must be PremarketVolumeProfile")
        key = (
            profile.symbol, profile.historical_cutoff,
            profile.profile_version,
        )
        with self._lock:
            self._curves[key] = profile
            self._curves.move_to_end(key)
            while len(self._curves) > self.maximum_curves:
                self._curves.popitem(last=False)

    def cached_profile(
        self,
        symbol: str,
        *,
        trading_date: date,
        profile_version: str = PREMARKET_PROFILE_VERSION,
    ) -> PremarketVolumeProfile | None:
        key = (_symbol(symbol), trading_date, str(profile_version))
        with self._lock:
            value = self._curves.get(key)
            if value is not None:
                self._curves.move_to_end(key)
            return value

    def lookup(
        self,
        symbol: str,
        *,
        timestamp: datetime,
        current_completed_premarket_volume: Decimal,
        legacy_rvol: Decimal | None,
        threshold: Decimal = Decimal("2"),
        profile_version: str = PREMARKET_PROFILE_VERSION,
    ) -> PremarketRvolShadowResult:
        """Return one descriptive comparison without changing any decision."""
        normalized = _symbol(symbol)
        local = _aware(timestamp).astimezone(EASTERN)
        current = Decimal(str(current_completed_premarket_volume))
        if not current.is_finite() or current < ZERO:
            raise ValueError("current_completed_premarket_volume must be non-negative")
        if not threshold.is_finite() or threshold <= ZERO:
            raise ValueError("threshold must be positive")
        legacy = None if legacy_rvol is None else Decimal(str(legacy_rvol))
        legacy_pass = None if legacy is None else legacy >= threshold

        # A lookup at 04:49 uses completed bars through 04:48.  This prevents
        # a partially formed current minute from being compared with a fully
        # completed historical minute.
        comparison = local.replace(second=0, microsecond=0) - timedelta(minutes=1)
        result: PremarketRvolShadowResult
        if not _is_premarket(local) or comparison.time() < PREMARKET_START:
            result = PremarketRvolShadowResult(
                normalized, comparison, "PREMARKET", legacy, None, 0,
                PremarketRvolShadowStatus.NOT_PREMARKET, legacy_pass, None,
                PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY,
            )
        else:
            prepared = self.cached_profile(
                normalized, trading_date=local.date(),
                profile_version=profile_version,
            )
            sample_count = 0 if prepared is None else prepared.session_count
            index = _premarket_minute(comparison)
            expected = (
                None
                if prepared is None or not prepared.expected_cumulative
                else prepared.expected_cumulative[index]
            )
            if expected is None or expected <= ZERO:
                result = PremarketRvolShadowResult(
                    normalized, comparison, "PREMARKET", legacy, None,
                    sample_count, PremarketRvolShadowStatus.UNAVAILABLE,
                    legacy_pass, None,
                    (
                        PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
                        if prepared is None else prepared.status
                    ),
                )
            else:
                normalized_rvol = current / expected
                result = PremarketRvolShadowResult(
                    normalized, comparison, "PREMARKET", legacy,
                    normalized_rvol, sample_count,
                    PremarketRvolShadowStatus.AVAILABLE, legacy_pass,
                    normalized_rvol >= threshold, prepared.status,
                )
        self._emit(result)
        return result

    def _emit(self, result: PremarketRvolShadowResult) -> None:
        if self._telemetry_sink is None:
            return
        try:
            self._telemetry_sink({
                "event": "rvol_shadow",
                "rvol_shadow.symbol": result.symbol,
                "rvol_shadow.legacy_value": result.legacy_rvol,
                "rvol_shadow.normalized_value": result.premarket_normalized_rvol,
                "rvol_shadow.sample_count": result.historical_sample_count,
                "rvol_shadow.legacy_pass": result.legacy_pass,
                "rvol_shadow.shadow_pass": result.shadow_pass,
                "rvol_shadow.profile_status": result.profile_status,
            })
        except Exception:
            # Descriptive telemetry can never affect production behavior.
            return


def _symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _is_premarket(value: datetime) -> bool:
    local_time = value.astimezone(EASTERN).time().replace(tzinfo=None)
    return PREMARKET_START <= local_time < REGULAR_SESSION_START


def _premarket_minute(value: datetime) -> int:
    local = value.astimezone(EASTERN)
    return (local.hour * 60 + local.minute) - (PREMARKET_START.hour * 60)


__all__ = [
    "DEFAULT_MINIMUM_HISTORICAL_SESSIONS",
    "PREMARKET_PROFILE_VERSION",
    "PremarketRvolShadow",
    "PremarketRvolShadowResult",
    "PremarketRvolShadowStatus",
    "PremarketVolumeProfile",
    "PremarketVolumeProfileStatus",
]
