"""Immutable runtime envelopes for off-path multi-strategy discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.opportunity_discovery import (
    CompletedBar,
    DiscoveryContext,
    FeatureCapabilities,
    PositionFocusTier,
)

DISCOVERY_RUNTIME_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AuthoritativePositionObservation:
    """Point-in-time reference to execution-owned state, not position ownership."""

    position_id: str
    source: str
    account_id: str
    position_key: str
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal | None
    observed_at: datetime
    lifecycle_id: str | None = None
    original_opportunity_id: str | None = None
    entry_strategy_id: str | None = None
    entry_strategy_version: str | None = None
    entry_timestamp: datetime | None = None
    entry_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity == 0:
            raise ValueError("authoritative open position quantity cannot be zero")
        if self.observed_at.tzinfo is None:
            raise ValueError("position observation time must be aware")
        if self.entry_timestamp is not None and self.entry_timestamp.tzinfo is None:
            raise ValueError("entry timestamp must be aware")
        if not all((self.position_id.strip(), self.source.strip(), self.account_id.strip(),
                    self.position_key.strip(), self.symbol.strip())):
            raise ValueError("authoritative position reference is incomplete")


@dataclass(frozen=True, slots=True)
class RuntimeDiscoveryObservation:
    context: DiscoveryContext
    observed_at: datetime
    focus_tier: PositionFocusTier
    authoritative_position: AuthoritativePositionObservation | None = None
    working_order_ids: tuple[str, ...] = ()
    schema_version: int = DISCOVERY_RUNTIME_SCHEMA_VERSION
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at != self.context.decision_cutoff:
            raise ValueError("discovery observation must preserve its decision cutoff")
        if not self.research_only:
            raise ValueError("runtime discovery is research-only")
        if self.authoritative_position is not None:
            if self.authoritative_position.symbol.upper() != self.context.symbol.upper():
                raise ValueError("position and discovery symbols must match")
            if self.focus_tier is not PositionFocusTier.OPEN_POSITION:
                raise ValueError("an authoritative position requires tier-one focus")
        elif self.working_order_ids and self.focus_tier is not PositionFocusTier.WORKING_ORDER:
            raise ValueError("working-order focus requires tier two")


@dataclass(frozen=True, slots=True)
class StrategyCoverage:
    strategy_id: str
    evaluations: int
    raw_detections: int
    unique_episodes: int
    normalized_opportunities: int


@dataclass(frozen=True, slots=True)
class DiscoveryTelemetry:
    market_observations: int = 0
    completed_bars: int = 0
    discovery_cycles: int = 0
    detector_evaluations: int = 0
    raw_detector_firings: int = 0
    unique_detector_episodes: int = 0
    normalized_opportunities: int = 0
    strategy_memberships: int = 0
    strategy_transitions: int = 0
    position_correlations: int = 0
    thesis_observations: int = 0
    add_on_candidates: int = 0
    callback_build_p50_ms: float = 0.0
    callback_build_p90_ms: float = 0.0
    callback_build_p99_ms: float = 0.0
    callback_build_max_ms: float = 0.0
    coverage: tuple[StrategyCoverage, ...] = ()


def discovery_observation_payload(value: RuntimeDiscoveryObservation) -> dict[str, Any]:
    return asdict(value)


def discovery_observation_from_dict(raw: dict[str, Any]) -> RuntimeDiscoveryObservation:
    context_raw = dict(raw["context"])
    capabilities = FeatureCapabilities(**{
        key: bool(value) for key, value in dict(context_raw.pop("capabilities")).items()
    })
    bars = tuple(CompletedBar(
        symbol=str(item["symbol"]), completed_at=_time(item["completed_at"]),
        open=Decimal(str(item["open"])), high=Decimal(str(item["high"])),
        low=Decimal(str(item["low"])), close=Decimal(str(item["close"])),
        volume=Decimal(str(item["volume"])), session=str(item.get("session", "REGULAR")),
    ) for item in context_raw.pop("completed_bars"))
    context = DiscoveryContext(
        symbol=str(context_raw["symbol"]),
        session_date=date.fromisoformat(str(context_raw["session_date"])),
        session=str(context_raw["session"]),
        decision_cutoff=_time(context_raw["decision_cutoff"]),
        completed_bars=bars,
        capabilities=capabilities,
        prior_close=_decimal(context_raw.get("prior_close")),
        vwap=_decimal(context_raw.get("vwap")),
        percentage_change=_decimal(context_raw.get("percentage_change")),
        relative_volume=_decimal(context_raw.get("relative_volume")),
        dollar_volume=_decimal(context_raw.get("dollar_volume")),
        spread_percent=_decimal(context_raw.get("spread_percent")),
        float_shares=_decimal(context_raw.get("float_shares")),
        scanner_rank=(None if context_raw.get("scanner_rank") is None else int(context_raw["scanner_rank"])),
    )
    position_raw = raw.get("authoritative_position")
    position = None
    if position_raw is not None:
        item = dict(position_raw)
        position = AuthoritativePositionObservation(
            position_id=str(item["position_id"]), source=str(item["source"]),
            account_id=str(item["account_id"]), position_key=str(item["position_key"]),
            symbol=str(item["symbol"]), quantity=Decimal(str(item["quantity"])),
            average_entry_price=_decimal(item.get("average_entry_price")),
            observed_at=_time(item["observed_at"]), lifecycle_id=item.get("lifecycle_id"),
            original_opportunity_id=item.get("original_opportunity_id"),
            entry_strategy_id=item.get("entry_strategy_id"),
            entry_strategy_version=item.get("entry_strategy_version"),
            entry_timestamp=(None if item.get("entry_timestamp") is None else _time(item["entry_timestamp"])),
            entry_price=_decimal(item.get("entry_price")),
        )
    return RuntimeDiscoveryObservation(
        context=context, observed_at=_time(raw["observed_at"]),
        focus_tier=PositionFocusTier(int(raw["focus_tier"])),
        authoritative_position=position,
        working_order_ids=tuple(str(item) for item in raw.get("working_order_ids", ())),
        schema_version=int(raw.get("schema_version", DISCOVERY_RUNTIME_SCHEMA_VERSION)),
        research_only=bool(raw.get("research_only", True)),
    )


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None:
        raise ValueError("serialized discovery timestamp must be aware")
    return result
