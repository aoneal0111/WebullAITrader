"""Bounded, point-in-time crypto completed-bar and regime evidence.

This module is an evidence adapter only.  It contains no strategy registry,
selection, sizing, broker, PAPER, LIVE, GUI, or persistence authority.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import ceil
from statistics import median
from typing import TypeVar

from app.assets import AssetType
from app.research_core import EvidenceProvenance

from .models import CryptoPair, CryptoResearchRegime, crypto_regime


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
DEFAULT_HISTORY_LIMIT = 64
DEFAULT_SYMBOL_LIMIT = 256
DEFAULT_CORRELATION_WINDOW = 20
DEFAULT_MIN_CORRELATION_OBSERVATIONS = 10


class CryptoBarInterval(StrEnum):
    """Intervals present in the installed official Webull SDK."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"

    @property
    def duration(self) -> timedelta:
        return {self.M1: timedelta(minutes=1), self.M5: timedelta(minutes=5), self.M15: timedelta(minutes=15)}[self]


SUPPORTED_CRYPTO_BAR_INTERVALS = tuple(CryptoBarInterval)


class CryptoRegimeLabel(StrEnum):
    BROAD_RISK_ON = "BROAD_RISK_ON"
    BTC_LED = "BTC_LED"
    ETH_LED = "ETH_LED"
    MIXED = "MIXED"
    BROAD_RISK_OFF = "BROAD_RISK_OFF"
    HIGH_DISPERSION = "HIGH_DISPERSION"
    LOW_ACTIVITY = "LOW_ACTIVITY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class MalformedCryptoBarError(ValueError):
    """A provider row could not be safely normalized."""


class FutureCryptoEvidenceError(ValueError):
    """A bar or derived value crosses its decision cutoff."""


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str, *, required: bool = True) -> Decimal | None:
    if value is None or not str(value).strip():
        if required:
            raise MalformedCryptoBarError(f"missing crypto bar field: {name}")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MalformedCryptoBarError(f"malformed crypto bar field: {name}") from exc
    if not result.is_finite():
        raise MalformedCryptoBarError(f"non-finite crypto bar field: {name}")
    return result


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        return _aware(value, name)
    if value is None or not str(value).strip():
        raise MalformedCryptoBarError(f"missing crypto bar field: {name}")
    try:
        numeric = Decimal(str(value))
        if numeric > Decimal("10000000000"):
            numeric /= Decimal("1000")
        return datetime.fromtimestamp(float(numeric), UTC)
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return _aware(parsed, name)
        except (ValueError, TypeError) as inner:
            raise MalformedCryptoBarError(f"malformed crypto bar field: {name}") from inner


def _first(row: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def _completion_flag(row: Mapping[str, object]) -> bool | None:
    for name in ("is_complete", "complete", "closed", "final", "completed"):
        if name not in row or row[name] is None:
            continue
        value = row[name]
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "closed", "final", "complete"}:
            return True
        if normalized in {"false", "0", "no", "open", "incomplete", "partial"}:
            return False
        raise MalformedCryptoBarError(f"malformed completion flag: {name}")
    if row.get("real_time") is True or row.get("realtime") is True:
        return False
    return None


@dataclass(frozen=True, slots=True)
class CryptoCompletedBar:
    asset_type: AssetType
    canonical_symbol: str
    provider_symbol: str
    interval: CryptoBarInterval
    opened_at: datetime
    closed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    provider: str
    observed_at: datetime
    decision_cutoff: datetime
    is_complete: bool = True

    def __post_init__(self) -> None:
        if self.asset_type is not AssetType.CRYPTO:
            raise ValueError("completed bar must be crypto")
        symbol = self.canonical_symbol.strip().upper()
        if "/" not in symbol or len(symbol.split("/")) != 2:
            raise ValueError("canonical crypto symbol must be BASE/QUOTE")
        object.__setattr__(self, "canonical_symbol", symbol)
        object.__setattr__(self, "provider_symbol", self.provider_symbol.strip().upper())
        object.__setattr__(self, "opened_at", _aware(self.opened_at, "opened_at"))
        object.__setattr__(self, "closed_at", _aware(self.closed_at, "closed_at"))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        cutoff = _aware(self.decision_cutoff, "decision_cutoff")
        object.__setattr__(self, "decision_cutoff", cutoff)
        if not self.provider_symbol or not self.provider.strip():
            raise ValueError("provider and provider_symbol are required")
        if self.closed_at <= self.opened_at:
            raise ValueError("closed_at must be after opened_at")
        if self.closed_at != self.opened_at + self.interval.duration:
            raise ValueError("bar timestamps must match the declared interval")
        if not self.is_complete:
            raise ValueError("incomplete bars cannot enter completed evidence")
        if self.closed_at > cutoff or self.observed_at > cutoff:
            raise FutureCryptoEvidenceError("completed bar exceeds decision cutoff")
        if min(self.open, self.high, self.low, self.close) <= ZERO:
            raise ValueError("crypto OHLC values must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("crypto OHLC geometry is malformed")
        if self.volume is not None and self.volume < ZERO:
            raise ValueError("crypto volume cannot be negative")

    @property
    def source_timestamp(self) -> datetime:
        return self.closed_at


def normalize_crypto_bar(
    pair: CryptoPair,
    row: Mapping[str, object],
    *,
    interval: CryptoBarInterval,
    observed_at: datetime,
    decision_cutoff: datetime,
    provider: str = "WEBULL_CRYPTO_HISTORY",
) -> CryptoCompletedBar | None:
    """Normalize one Webull row; ``None`` means explicitly/currently incomplete."""

    opened = _timestamp(_first(row, "timestamp", "time", "bar_time", "open_time", "start_time"), "timestamp")
    explicit_closed = _first(row, "closed_at", "end_time", "end_timestamp")
    closed = interval.duration + opened if explicit_closed is None else _timestamp(explicit_closed, "closed_at")
    flag = _completion_flag(row)
    cutoff = _aware(decision_cutoff, "decision_cutoff")
    if flag is False or closed > cutoff:
        return None
    return CryptoCompletedBar(
        asset_type=AssetType.CRYPTO,
        canonical_symbol=pair.canonical_symbol,
        provider_symbol=pair.provider_symbol,
        interval=interval,
        opened_at=opened,
        closed_at=closed,
        open=_decimal(_first(row, "open", "open_price"), "open") or ZERO,
        high=_decimal(_first(row, "high", "high_price"), "high") or ZERO,
        low=_decimal(_first(row, "low", "low_price"), "low") or ZERO,
        close=_decimal(_first(row, "close", "close_price"), "close") or ZERO,
        volume=_decimal(_first(row, "volume", "turnover_volume", "trade_volume"), "volume", required=False),
        provider=provider,
        observed_at=observed_at,
        decision_cutoff=cutoff,
    )


@dataclass(frozen=True, slots=True)
class CryptoBarHistoryMetrics:
    crypto_bar_symbols: int
    crypto_bar_series: int
    crypto_completed_bars_retained: int
    crypto_evidence_duplicates_suppressed: int


@dataclass(frozen=True, slots=True)
class CryptoBarNormalizationResult:
    bars: tuple[CryptoCompletedBar, ...]
    malformed_rows: int
    incomplete_rows: int
    duplicate_rows: int


def normalize_crypto_rows(
    pair: CryptoPair,
    rows: Sequence[Mapping[str, object]],
    *,
    interval: CryptoBarInterval,
    observed_at: datetime,
    decision_cutoff: datetime,
    provider: str = "WEBULL_CRYPTO_HISTORY",
) -> CryptoBarNormalizationResult:
    """Contain malformed rows and return deterministic, duplicate-free bars."""

    normalized: dict[datetime, CryptoCompletedBar] = {}
    malformed = incomplete = duplicates = 0
    for row in rows:
        try:
            item = normalize_crypto_bar(
                pair, row, interval=interval, observed_at=observed_at,
                decision_cutoff=decision_cutoff, provider=provider,
            )
        except (MalformedCryptoBarError, FutureCryptoEvidenceError, ValueError, TypeError):
            malformed += 1
            continue
        if item is None:
            incomplete += 1
            continue
        if item.opened_at in normalized:
            duplicates += 1
            continue
        normalized[item.opened_at] = item
    return CryptoBarNormalizationResult(
        tuple(normalized[key] for key in sorted(normalized)), malformed, incomplete, duplicates
    )


class BoundedCryptoBarHistory:
    """Bounded history keyed by canonical symbol and interval."""

    def __init__(self, *, maximum_symbols: int = DEFAULT_SYMBOL_LIMIT, bars_per_series: int = DEFAULT_HISTORY_LIMIT) -> None:
        if maximum_symbols <= 0 or bars_per_series <= 0:
            raise ValueError("crypto history bounds must be positive")
        self.maximum_symbols = maximum_symbols
        self.bars_per_series = bars_per_series
        self._series: OrderedDict[tuple[str, CryptoBarInterval], deque[CryptoCompletedBar]] = OrderedDict()
        self._duplicates = 0

    def add(self, bar: CryptoCompletedBar) -> bool:
        key = (bar.canonical_symbol, bar.interval)
        if key not in self._series and bar.canonical_symbol not in {item[0] for item in self._series} and len({item[0] for item in self._series}) >= self.maximum_symbols:
            return False
        values = self._series.setdefault(key, deque(maxlen=self.bars_per_series))
        if any(item.opened_at == bar.opened_at for item in values):
            self._duplicates += 1
            return False
        values.append(bar)
        ordered = sorted(values, key=lambda item: item.opened_at)
        values.clear()
        values.extend(ordered[-self.bars_per_series:])
        self._series.move_to_end(key)
        return True

    def add_many(self, bars: Sequence[CryptoCompletedBar]) -> int:
        return sum(1 for item in bars if self.add(item))

    def bars(self, symbol: str, interval: CryptoBarInterval) -> tuple[CryptoCompletedBar, ...]:
        return tuple(self._series.get((symbol.strip().upper(), interval), ()))

    def all_series(self) -> tuple[tuple[str, CryptoBarInterval, tuple[CryptoCompletedBar, ...]], ...]:
        return tuple((symbol, interval, tuple(values)) for (symbol, interval), values in self._series.items())

    def metrics(self) -> CryptoBarHistoryMetrics:
        return CryptoBarHistoryMetrics(
            crypto_bar_symbols=len({symbol for symbol, _ in self._series}),
            crypto_bar_series=len(self._series),
            crypto_completed_bars_retained=sum(len(values) for values in self._series.values()),
            crypto_evidence_duplicates_suppressed=self._duplicates,
        )


class CryptoEvidenceLedger:
    """Bounded evidence owner exposing scalar telemetry only."""

    def __init__(self, *, maximum_symbols: int = DEFAULT_SYMBOL_LIMIT, bars_per_series: int = DEFAULT_HISTORY_LIMIT) -> None:
        self.history = BoundedCryptoBarHistory(maximum_symbols=maximum_symbols, bars_per_series=bars_per_series)
        self._benchmark_updates = 0
        self._regime_updates = 0
        self._breadth_sample_size = 0
        self._relative_strength_symbols = 0
        self._failures = 0

    def add_bars(self, bars: Sequence[CryptoCompletedBar]) -> int:
        return self.history.add_many(bars)

    def record_benchmark(self) -> None:
        self._benchmark_updates += 1

    def record_regime(self) -> None:
        self._regime_updates += 1

    def record_breadth(self, sample_size: int) -> None:
        self._breadth_sample_size = max(0, int(sample_size))

    def record_relative_strength(self, symbol_count: int) -> None:
        self._relative_strength_symbols = max(0, int(symbol_count))

    def record_failure(self) -> None:
        self._failures += 1

    def metrics(self) -> CryptoEvidenceMetrics:
        history = self.history.metrics()
        return CryptoEvidenceMetrics(
            history.crypto_bar_symbols, history.crypto_bar_series,
            history.crypto_completed_bars_retained, self._benchmark_updates,
            self._regime_updates, self._breadth_sample_size,
            self._relative_strength_symbols, self._failures,
            history.crypto_evidence_duplicates_suppressed,
        )


@dataclass(frozen=True, slots=True)
class CryptoBenchmarkEvidence:
    canonical_symbol: str
    interval: CryptoBarInterval
    decision_cutoff: datetime
    sample_size: int
    return_percent: Decimal | None
    trend: Decimal | None
    volatility: Decimal | None
    momentum_velocity: Decimal | None
    high_low_location: Decimal | None
    source_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoBreadthEvidence:
    interval: CryptoBarInterval
    decision_cutoff: datetime
    observed_universe_size: int
    eligible_sample_size: int
    coverage_percent: Decimal | None
    advancing_count: int
    declining_count: int
    unchanged_count: int
    advancing_percent: Decimal | None
    declining_percent: Decimal | None
    positive_return_percent: Decimal | None
    negative_return_percent: Decimal | None
    median_return_percent: Decimal | None
    eligible_returns: tuple[tuple[str, Decimal], ...]
    source_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoDispersionEvidence:
    interval: CryptoBarInterval
    decision_cutoff: datetime
    sample_size: int
    standard_deviation: Decimal | None
    median_absolute_deviation: Decimal | None
    source_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoCorrelationEvidence:
    canonical_symbol: str
    benchmark_symbol: str
    interval: CryptoBarInterval
    decision_cutoff: datetime
    observation_count: int
    minimum_observations: int
    correlation: Decimal | None
    aligned_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoRelativeStrengthEvidence:
    canonical_symbol: str
    benchmark_symbol: str
    interval: CryptoBarInterval
    decision_cutoff: datetime
    horizon_bars: int
    symbol_return_percent: Decimal | None
    benchmark_return_percent: Decimal | None
    excess_return_percent: Decimal | None
    source_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoRegimeEvidence:
    window: CryptoResearchRegime
    label: CryptoRegimeLabel
    decision_cutoff: datetime
    btc_return_percent: Decimal | None
    eth_return_percent: Decimal | None
    breadth_advancing_percent: Decimal | None
    median_return_percent: Decimal | None
    dispersion: Decimal | None
    source_timestamps: tuple[datetime, ...]
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class CryptoEvidenceMetrics:
    crypto_bar_symbols: int
    crypto_bar_series: int
    crypto_completed_bars_retained: int
    crypto_benchmark_updates: int
    crypto_regime_updates: int
    crypto_breadth_sample_size: int
    crypto_relative_strength_symbols: int
    crypto_evidence_failures: int
    crypto_evidence_duplicates_suppressed: int


@dataclass(frozen=True, slots=True)
class CryptoEvidenceContext:
    canonical_symbol: str
    provider_symbol: str
    decision_cutoff: datetime
    regime_window: CryptoResearchRegime
    bars_by_interval: tuple[tuple[CryptoBarInterval, tuple[CryptoCompletedBar, ...]], ...]
    btc_benchmark: CryptoBenchmarkEvidence
    eth_benchmark: CryptoBenchmarkEvidence
    breadth: CryptoBreadthEvidence
    dispersion: CryptoDispersionEvidence
    btc_correlation: CryptoCorrelationEvidence
    eth_correlation: CryptoCorrelationEvidence
    btc_relative_strength: CryptoRelativeStrengthEvidence
    eth_relative_strength: CryptoRelativeStrengthEvidence
    regime: CryptoRegimeEvidence
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("crypto evidence context must remain research-only")
        if "/" not in self.canonical_symbol or not self.provider_symbol.strip():
            raise ValueError("crypto evidence context requires canonical/provider identity")
        _aware(self.decision_cutoff, "decision_cutoff")


@dataclass(frozen=True, slots=True)
class CryptoHistoryRequest:
    symbols: tuple[CryptoPair, ...]
    interval: CryptoBarInterval
    count: int = DEFAULT_HISTORY_LIMIT


class CryptoHistoryRefreshPlanner:
    """Round-robin one interval per cadence; avoids 256x3 per-minute bursts."""

    def __init__(self, pairs: Sequence[CryptoPair], *, batch_size: int = 20, cadence_seconds: int = 60) -> None:
        if not 1 <= batch_size <= 256 or cadence_seconds <= 0:
            raise ValueError("history planner bounds are invalid")
        unique = {item.canonical_symbol: item for item in pairs}
        if len(unique) > DEFAULT_SYMBOL_LIMIT:
            raise ValueError("history planner supports at most 256 symbols")
        priority = tuple(
            unique[key] for key in ("BTC/USD", "ETH/USD") if key in unique
        )
        remainder = tuple(unique[key] for key in sorted(unique) if key not in {item.canonical_symbol for item in priority})
        self.pairs = priority + remainder
        self.batch_size = batch_size
        self.cadence_seconds = cadence_seconds

    def plan(self, cycle_index: int) -> tuple[CryptoHistoryRequest, ...]:
        interval = SUPPORTED_CRYPTO_BAR_INTERVALS[cycle_index % len(SUPPORTED_CRYPTO_BAR_INTERVALS)]
        return tuple(
            CryptoHistoryRequest(self.pairs[offset : offset + self.batch_size], interval)
            for offset in range(0, len(self.pairs), self.batch_size)
        )

    @property
    def requests_per_cycle(self) -> int:
        return ceil(len(self.pairs) / self.batch_size)

    @property
    def requests_per_second(self) -> Decimal:
        return Decimal(self.requests_per_cycle) / Decimal(self.cadence_seconds)


def _bar_returns(bars: Sequence[CryptoCompletedBar], horizon_bars: int) -> tuple[Decimal, tuple[datetime, ...]] | None:
    ordered = tuple(sorted(bars, key=lambda item: item.closed_at))
    if horizon_bars <= 0 or len(ordered) < horizon_bars + 1:
        return None
    sample = ordered[-(horizon_bars + 1):]
    first, last = sample[0].close, sample[-1].close
    if first <= ZERO:
        return None
    return ((last / first - ONE) * HUNDRED, tuple(item.closed_at for item in sample))


def _provenance(cutoff: datetime, timestamps: Sequence[datetime]) -> EvidenceProvenance:
    cutoff = _aware(cutoff, "decision_cutoff")
    source = tuple(sorted(set(_aware(item, "source timestamp") for item in timestamps)))
    observed = max(source, default=cutoff)
    if observed > cutoff:
        raise FutureCryptoEvidenceError("derived evidence uses a future source timestamp")
    return EvidenceProvenance(
        observed_at=observed,
        decision_cutoff=cutoff,
        evidence_timestamps=tuple((f"source_{index}", item) for index, item in enumerate(source)),
    )


def _standard_deviation(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    average = sum(values, ZERO) / Decimal(len(values))
    return (sum(((item - average) ** 2 for item in values), ZERO) / Decimal(len(values) - 1)).sqrt()


def benchmark_evidence(
    pair: CryptoPair,
    bars: Sequence[CryptoCompletedBar],
    *,
    interval: CryptoBarInterval,
    decision_cutoff: datetime,
    window_bars: int = 20,
) -> CryptoBenchmarkEvidence:
    ordered = tuple(item for item in sorted(bars, key=lambda value: value.closed_at) if item.closed_at <= _aware(decision_cutoff, "decision_cutoff"))[-window_bars:]
    returns = tuple((ordered[index].close / ordered[index - 1].close - ONE) for index in range(1, len(ordered))) if len(ordered) >= 2 else ()
    change = _bar_returns(ordered, min(window_bars - 1, len(ordered) - 1)) if len(ordered) >= 2 else None
    return_percent = None if change is None else change[0]
    duration = (ordered[-1].closed_at - ordered[0].closed_at).total_seconds() / 60 if len(ordered) >= 2 else 0
    velocity = None if return_percent is None or duration <= 0 else return_percent / Decimal(str(duration))
    high = max((item.high for item in ordered), default=None)
    low = min((item.low for item in ordered), default=None)
    location = None if high is None or low is None or high == low else (ordered[-1].close - low) / (high - low)
    timestamps = tuple(item.closed_at for item in ordered)
    return CryptoBenchmarkEvidence(pair.canonical_symbol, interval, _aware(decision_cutoff, "decision_cutoff"), len(ordered), return_percent, return_percent, None if not returns else _standard_deviation(returns), velocity, location, timestamps, _provenance(decision_cutoff, timestamps))


def _latest_return(bars: Sequence[CryptoCompletedBar], horizon_bars: int, cutoff: datetime) -> tuple[Decimal, tuple[datetime, ...]] | None:
    eligible = tuple(item for item in bars if item.closed_at <= _aware(cutoff, "decision_cutoff"))
    return _bar_returns(eligible, horizon_bars)


def breadth_evidence(
    series: Mapping[str, Sequence[CryptoCompletedBar]],
    *,
    interval: CryptoBarInterval,
    decision_cutoff: datetime,
    observed_universe_size: int,
    horizon_bars: int = 1,
) -> CryptoBreadthEvidence:
    if observed_universe_size < 0:
        raise ValueError("observed universe cannot be negative")
    eligible: list[tuple[str, Decimal, tuple[datetime, ...]]] = []
    for symbol, bars in sorted(series.items()):
        value = _latest_return(bars, horizon_bars, decision_cutoff)
        if value is not None:
            eligible.append((symbol.upper(), value[0], value[1]))
    values = tuple(item[1] for item in eligible)
    advancing = sum(item > ZERO for item in values)
    declining = sum(item < ZERO for item in values)
    unchanged = len(values) - advancing - declining
    sample = len(values)
    coverage = None if observed_universe_size == 0 else Decimal(sample) / Decimal(observed_universe_size) * HUNDRED
    timestamps = tuple(timestamp for _, _, source in eligible for timestamp in source)
    return CryptoBreadthEvidence(
        interval, _aware(decision_cutoff, "decision_cutoff"), observed_universe_size, sample,
        coverage, advancing, declining, unchanged,
        None if not sample else Decimal(advancing) / Decimal(sample) * HUNDRED,
        None if not sample else Decimal(declining) / Decimal(sample) * HUNDRED,
        None if not sample else Decimal(sum(item > ZERO for item in values)) / Decimal(sample) * HUNDRED,
        None if not sample else Decimal(sum(item < ZERO for item in values)) / Decimal(sample) * HUNDRED,
        None if not sample else median(values),
        tuple((symbol, value) for symbol, value, _ in eligible),
        tuple(sorted(set(timestamps))), _provenance(decision_cutoff, timestamps),
    )


def dispersion_evidence(
    breadth: CryptoBreadthEvidence,
    *,
    minimum_sample: int = 2,
) -> CryptoDispersionEvidence:
    values = tuple(value for _, value in breadth.eligible_returns)
    standard = _standard_deviation(values) if len(values) >= minimum_sample else None
    mad = None
    if len(values) >= minimum_sample:
        middle = median(values)
        mad = median(tuple(abs(value - middle) for value in values))
    return CryptoDispersionEvidence(
        breadth.interval, breadth.decision_cutoff, len(values), standard, mad,
        breadth.source_timestamps, _provenance(breadth.decision_cutoff, breadth.source_timestamps),
    )


def _return_map(bars: Sequence[CryptoCompletedBar]) -> dict[datetime, Decimal]:
    ordered = tuple(sorted(bars, key=lambda item: item.closed_at))
    return {
        ordered[index].closed_at: ordered[index].close / ordered[index - 1].close - ONE
        for index in range(1, len(ordered))
        if ordered[index - 1].close > ZERO
    }


def correlation_evidence(
    bars: Sequence[CryptoCompletedBar],
    benchmark_bars: Sequence[CryptoCompletedBar],
    *,
    canonical_symbol: str,
    benchmark_symbol: str,
    interval: CryptoBarInterval,
    decision_cutoff: datetime,
    window_bars: int = DEFAULT_CORRELATION_WINDOW,
    minimum_observations: int = DEFAULT_MIN_CORRELATION_OBSERVATIONS,
) -> CryptoCorrelationEvidence:
    left, right = _return_map(bars), _return_map(benchmark_bars)
    timestamps = tuple(sorted(set(left).intersection(right)))[-window_bars:]
    correlation = None
    if len(timestamps) >= minimum_observations:
        left_values = tuple(left[item] for item in timestamps)
        right_values = tuple(right[item] for item in timestamps)
        left_mean, right_mean = sum(left_values, ZERO) / Decimal(len(left_values)), sum(right_values, ZERO) / Decimal(len(right_values))
        numerator = sum((((a - left_mean) * (b - right_mean)) for a, b in zip(left_values, right_values)), ZERO)
        left_var = sum((((a - left_mean) ** 2) for a in left_values), ZERO)
        right_var = sum((((b - right_mean) ** 2) for b in right_values), ZERO)
        denominator = (left_var * right_var).sqrt()
        correlation = None if denominator == ZERO else numerator / denominator
    return CryptoCorrelationEvidence(
        canonical_symbol, benchmark_symbol, interval, _aware(decision_cutoff, "decision_cutoff"),
        len(timestamps), minimum_observations, correlation, timestamps,
        _provenance(decision_cutoff, timestamps),
    )


def relative_strength_evidence(
    bars: Sequence[CryptoCompletedBar],
    benchmark_bars: Sequence[CryptoCompletedBar],
    *,
    canonical_symbol: str,
    benchmark_symbol: str,
    interval: CryptoBarInterval,
    decision_cutoff: datetime,
    horizon_bars: int = 5,
) -> CryptoRelativeStrengthEvidence:
    symbol_value = _latest_return(bars, horizon_bars, decision_cutoff)
    benchmark_value = _latest_return(benchmark_bars, horizon_bars, decision_cutoff)
    symbol_return = None if symbol_value is None else symbol_value[0]
    benchmark_return = None if benchmark_value is None else benchmark_value[0]
    excess = None if symbol_return is None or benchmark_return is None else symbol_return - benchmark_return
    timestamps = tuple(sorted(set((symbol_value or (ZERO, ()))[1]) | set((benchmark_value or (ZERO, ()))[1])))
    return CryptoRelativeStrengthEvidence(
        canonical_symbol, benchmark_symbol, interval, _aware(decision_cutoff, "decision_cutoff"),
        horizon_bars, symbol_return,
        benchmark_return,
        excess,
        timestamps, _provenance(decision_cutoff, timestamps),
    )


def regime_evidence(
    btc: CryptoBenchmarkEvidence,
    eth: CryptoBenchmarkEvidence,
    breadth: CryptoBreadthEvidence,
    dispersion: CryptoDispersionEvidence,
    *,
    at: datetime,
    leadership_gap_percent: Decimal = Decimal("1"),
    high_dispersion_percent: Decimal = Decimal("5"),
) -> CryptoRegimeEvidence:
    btc_return = btc.return_percent
    eth_return = eth.return_percent
    median_return = breadth.median_return_percent
    advancing = breadth.advancing_percent
    source = tuple(sorted(set(btc.source_timestamps + eth.source_timestamps + breadth.source_timestamps)))
    label = CryptoRegimeLabel.INSUFFICIENT_EVIDENCE
    if btc_return is not None and eth_return is not None and breadth.eligible_sample_size >= 2:
        if dispersion.standard_deviation is not None and dispersion.standard_deviation >= high_dispersion_percent:
            label = CryptoRegimeLabel.HIGH_DISPERSION
        elif advancing is not None and median_return is not None and advancing >= Decimal("60") and btc_return > ZERO and eth_return > ZERO:
            label = CryptoRegimeLabel.BROAD_RISK_ON
        elif breadth.declining_percent is not None and median_return is not None and breadth.declining_percent >= Decimal("60") and btc_return < ZERO and eth_return < ZERO:
            label = CryptoRegimeLabel.BROAD_RISK_OFF
        elif btc_return - eth_return >= leadership_gap_percent:
            label = CryptoRegimeLabel.BTC_LED
        elif eth_return - btc_return >= leadership_gap_percent:
            label = CryptoRegimeLabel.ETH_LED
        elif btc_return == ZERO and eth_return == ZERO and median_return == ZERO:
            label = CryptoRegimeLabel.LOW_ACTIVITY
        else:
            label = CryptoRegimeLabel.MIXED
    return CryptoRegimeEvidence(
        crypto_regime(at), label, _aware(at, "decision_cutoff"), btc_return, eth_return,
        advancing, median_return, dispersion.standard_deviation, source,
        _provenance(at, source),
    )


def build_crypto_evidence_context(
    pair: CryptoPair,
    history: BoundedCryptoBarHistory,
    *,
    decision_cutoff: datetime,
    interval: CryptoBarInterval = CryptoBarInterval.M1,
    observed_universe_size: int | None = None,
    benchmark_window_bars: int = 20,
    relative_strength_horizon_bars: int = 5,
) -> CryptoEvidenceContext:
    """Compose one symbol's bounded evidence view from an existing history owner."""

    cutoff = _aware(decision_cutoff, "decision_cutoff")
    btc_pair = CryptoPair.configured("BTC/USD")
    eth_pair = CryptoPair.configured("ETH/USD")
    symbol_bars = history.bars(pair.canonical_symbol, interval)
    btc_bars = history.bars(btc_pair.canonical_symbol, interval)
    eth_bars = history.bars(eth_pair.canonical_symbol, interval)
    series = {
        symbol: values
        for symbol, current_interval, values in history.all_series()
        if current_interval is interval
    }
    breadth = breadth_evidence(
        series,
        interval=interval,
        decision_cutoff=cutoff,
        observed_universe_size=(
            history.metrics().crypto_bar_symbols
            if observed_universe_size is None
            else observed_universe_size
        ),
    )
    dispersion = dispersion_evidence(breadth)
    btc = benchmark_evidence(
        btc_pair, btc_bars, interval=interval, decision_cutoff=cutoff,
        window_bars=benchmark_window_bars,
    )
    eth = benchmark_evidence(
        eth_pair, eth_bars, interval=interval, decision_cutoff=cutoff,
        window_bars=benchmark_window_bars,
    )
    return CryptoEvidenceContext(
        pair.canonical_symbol,
        pair.provider_symbol,
        cutoff,
        crypto_regime(cutoff),
        ((interval, symbol_bars),),
        btc,
        eth,
        breadth,
        dispersion,
        correlation_evidence(
            symbol_bars, btc_bars, canonical_symbol=pair.canonical_symbol,
            benchmark_symbol=btc_pair.canonical_symbol, interval=interval,
            decision_cutoff=cutoff,
        ),
        correlation_evidence(
            symbol_bars, eth_bars, canonical_symbol=pair.canonical_symbol,
            benchmark_symbol=eth_pair.canonical_symbol, interval=interval,
            decision_cutoff=cutoff,
        ),
        relative_strength_evidence(
            symbol_bars, btc_bars, canonical_symbol=pair.canonical_symbol,
            benchmark_symbol=btc_pair.canonical_symbol, interval=interval,
            decision_cutoff=cutoff, horizon_bars=relative_strength_horizon_bars,
        ),
        relative_strength_evidence(
            symbol_bars, eth_bars, canonical_symbol=pair.canonical_symbol,
            benchmark_symbol=eth_pair.canonical_symbol, interval=interval,
            decision_cutoff=cutoff, horizon_bars=relative_strength_horizon_bars,
        ),
        regime_evidence(btc, eth, breadth, dispersion, at=cutoff),
    )


__all__ = [
    "BoundedCryptoBarHistory", "CryptoBarHistoryMetrics", "CryptoBarInterval", "CryptoBarNormalizationResult",
    "CryptoBenchmarkEvidence", "CryptoBreadthEvidence", "CryptoCompletedBar",
    "CryptoCorrelationEvidence", "CryptoDispersionEvidence", "CryptoEvidenceContext",
    "CryptoEvidenceLedger", "CryptoEvidenceMetrics", "CryptoHistoryRefreshPlanner", "CryptoHistoryRequest",
    "CryptoRegimeEvidence", "CryptoRegimeLabel", "CryptoRelativeStrengthEvidence",
    "FutureCryptoEvidenceError", "MalformedCryptoBarError", "SUPPORTED_CRYPTO_BAR_INTERVALS",
    "benchmark_evidence", "breadth_evidence", "build_crypto_evidence_context", "correlation_evidence", "dispersion_evidence",
    "normalize_crypto_bar", "normalize_crypto_rows", "regime_evidence", "relative_strength_evidence",
]
