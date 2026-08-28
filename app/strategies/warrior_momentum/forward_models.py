"""Versioned, sanitized contracts for Warrior V1 forward paper capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from app.momentum_scanner.models import ScannerObservation

from .models import MinuteBar

CAPTURE_SCHEMA_VERSION = 1
_SENSITIVE_PARTS = (
    "secret", "token", "credential", "password", "account_id", "session_id",
    "access_key", "app_key", "api_key",
)


class CaptureRecordType(StrEnum):
    OBSERVATION_SESSION = "OBSERVATION_SESSION"
    DISCOVERY = "DISCOVERY"
    STATE_TRANSITION = "STATE_TRANSITION"
    MINUTE_BAR = "MINUTE_BAR"
    DECISION = "DECISION"
    CATALYST_EVIDENCE = "CATALYST_EVIDENCE"
    SPREAD_EVIDENCE = "SPREAD_EVIDENCE"
    DATA_QUALITY = "DATA_QUALITY"
    PAPER_FILL = "PAPER_FILL"
    MANAGEMENT_CONTEXT = "MANAGEMENT_CONTEXT"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    DAILY_REPORT = "DAILY_REPORT"
    SHADOW_EVALUATION = "SHADOW_EVALUATION"
    SHADOW_OUTCOME = "SHADOW_OUTCOME"
    SHADOW_POLICY_RESULT = "SHADOW_POLICY_RESULT"


class ForwardTransition(StrEnum):
    DISCOVERED = "DISCOVERED"
    WATCH = "WATCH"
    NEAR = "NEAR"
    QUALIFIED = "QUALIFIED"
    SETUP_FORMING = "SETUP_FORMING"
    SETUP_TRIGGERED = "SETUP_TRIGGERED"
    AWAITING_EXECUTION_DATA = "AWAITING_EXECUTION_DATA"
    ENTRY_READY = "ENTRY_READY"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    PAPER_ENTRY = "PAPER_ENTRY"
    PAPER_PARTIAL = "PAPER_PARTIAL"
    PAPER_EXIT = "PAPER_EXIT"


class FloatProvenance(StrEnum):
    AUTHORITATIVE_FLOAT = "AUTHORITATIVE_FLOAT"
    SHARES_OUTSTANDING = "SHARES_OUTSTANDING"
    MARKET_CAP_PRICE_PROXY = "MARKET_CAP_PRICE_PROXY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PointInTimeObservation:
    observation: ScannerObservation
    session: str
    bars: tuple[MinuteBar, ...]
    float_provenance: FloatProvenance = FloatProvenance.UNKNOWN
    catalyst_event_timestamp: datetime | None = None
    catalyst_event_date: date | None = None
    catalyst_source: str = "WEBULL_EARNINGS_SEC"
    catalyst_source_classification: str = "PRODUCTION_MARKET_DATA"
    quote_observed_at: datetime | None = None
    quote_freshness_seconds: Decimal | None = None
    last_price_observed_at: datetime | None = None
    last_price_freshness_seconds: Decimal | None = None
    evaluation_timestamp: datetime | None = None
    halt_state_known: bool = True
    volume_known: bool = True
    historical_bars_available: bool = True
    scanner_rank: int | None = None
    scanner_score: int | None = None
    scanner_classification: str | None = None
    scanner_failed_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.observation.timestamp.tzinfo is None:
            raise ValueError("observation timestamp must be timezone-aware")
        for name in (
            "catalyst_event_timestamp", "quote_observed_at",
            "last_price_observed_at",
            "evaluation_timestamp",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.quote_freshness_seconds is not None and self.quote_freshness_seconds < 0:
            raise ValueError("quote freshness cannot be negative")
        if (
            self.last_price_freshness_seconds is not None
            and self.last_price_freshness_seconds < 0
        ):
            raise ValueError("last price freshness cannot be negative")
        if self.scanner_rank is not None and self.scanner_rank <= 0:
            raise ValueError("scanner rank must be positive when available")


@dataclass(frozen=True, slots=True)
class PaperAccountContext:
    equity: Decimal
    buying_power: Decimal
    allowed_symbols: frozenset[str]
    existing_exposure: Decimal = Decimal("0")
    exposure_limit: Decimal | None = None
    risk_engine_approved: bool = True
    broker_restriction: bool = False


@dataclass(frozen=True, slots=True)
class ForwardCaptureConfiguration:
    """Operational capture settings; these do not alter strategy behavior."""

    storage_path: Path = Path(
        "data/warrior_momentum_v1_forward/forward_capture.sqlite3"
    )
    queue_capacity: int = 4096
    batch_size: int = 128
    flush_interval_seconds: float = 0.25
    quote_stale_after_seconds: Decimal = Decimal("5")
    counterfactual_bars: int = 60
    shadow_analysis_enabled: bool = True

    def __post_init__(self) -> None:
        if (
            self.queue_capacity <= 0 or self.batch_size <= 0
            or self.flush_interval_seconds <= 0
            or self.quote_stale_after_seconds < 0
            or self.counterfactual_bars <= 0
        ):
            raise ValueError("forward capture settings are invalid")


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    schema_version: int
    record_id: str
    record_type: CaptureRecordType
    symbol: str
    timestamp: datetime
    payload_json: str

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("capture payload must be an object")
        return value

    @classmethod
    def create(
        cls, record_type: CaptureRecordType, symbol: str, timestamp: datetime,
        payload: Mapping[str, Any], *, identity_parts: tuple[str, ...] = (),
    ) -> "CaptureRecord":
        normalized = symbol.strip().upper()
        if not normalized or timestamp.tzinfo is None:
            raise ValueError("record symbol and aware timestamp are required")
        _reject_sensitive(payload)
        payload_json = canonical_json(payload)
        identity = "|".join((str(CAPTURE_SCHEMA_VERSION), record_type.value, normalized,
                             timestamp.isoformat(), *identity_parts, payload_json))
        record_id = sha256(identity.encode("utf-8")).hexdigest()
        return cls(CAPTURE_SCHEMA_VERSION, record_id, record_type, normalized, timestamp, payload_json)


@dataclass(frozen=True, slots=True)
class CaptureMetrics:
    queue_depth: int
    records_written: int
    batches_written: int
    average_write_latency_ms: Decimal
    maximum_write_latency_ms: Decimal
    dropped_records: int
    duplicate_records: int
    gui_refresh_count: int
    gui_refresh_frequency_hz: Decimal
    synchronous_fallback_records: int = 0


def canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("captured timestamps must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (frozenset, set)):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise TypeError(f"unsupported capture value: {type(value).__name__}")


def _reject_sensitive(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if any(part in name for part in _SENSITIVE_PARTS):
                raise ValueError(f"sensitive field is forbidden: {path}.{key}")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{path}[{index}]")


__all__ = [
    "CAPTURE_SCHEMA_VERSION", "CaptureRecordType", "ForwardTransition",
    "FloatProvenance", "PointInTimeObservation", "PaperAccountContext",
    "ForwardCaptureConfiguration",
    "CaptureRecord", "CaptureMetrics", "canonical_json",
]
