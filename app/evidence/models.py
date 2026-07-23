from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from app.evidence.enums import EvidenceCategory, SignalDirection
from app.evidence.exceptions import EvidenceValidationError


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class Evidence:
    symbol: str
    timestamp: datetime
    source: str
    category: EvidenceCategory
    direction: SignalDirection
    confidence: float
    strength: float
    explanation: str
    features: Mapping[str, JSONValue] = field(default_factory=dict)
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    evidence_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))
        object.__setattr__(self, "source", _normalize_source(self.source))
        object.__setattr__(
            self,
            "explanation",
            _normalize_explanation(self.explanation),
        )
        object.__setattr__(
            self,
            "timestamp",
            _normalize_timestamp(self.timestamp),
        )
        object.__setattr__(
            self,
            "confidence",
            _normalize_unit_interval("confidence", self.confidence),
        )
        object.__setattr__(
            self,
            "strength",
            _normalize_unit_interval("strength", self.strength),
        )
        object.__setattr__(
            self,
            "features",
            _freeze_mapping("features", self.features),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping("metadata", self.metadata),
        )

        if not isinstance(self.category, EvidenceCategory):
            raise EvidenceValidationError(
                "category must be an EvidenceCategory"
            )

        if not isinstance(self.direction, SignalDirection):
            raise EvidenceValidationError(
                "direction must be a SignalDirection"
            )

        if not isinstance(self.evidence_id, UUID):
            raise EvidenceValidationError(
                "evidence_id must be a UUID"
            )

    @property
    def directional_score(self) -> float:
        return (
            self.direction.polarity
            * self.confidence
            * self.strength
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": str(self.evidence_id),
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "category": self.category.value,
            "direction": self.direction.value,
            "confidence": self.confidence,
            "strength": self.strength,
            "explanation": self.explanation,
            "features": _thaw_value(self.features),
            "metadata": _thaw_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Evidence":
        try:
            return cls(
                evidence_id=UUID(str(value["evidence_id"])),
                symbol=str(value["symbol"]),
                timestamp=datetime.fromisoformat(
                    str(value["timestamp"])
                ),
                source=str(value["source"]),
                category=EvidenceCategory(str(value["category"])),
                direction=SignalDirection(str(value["direction"])),
                confidence=float(value["confidence"]),
                strength=float(value["strength"]),
                explanation=str(value["explanation"]),
                features=value.get("features", {}),
                metadata=value.get("metadata", {}),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise EvidenceValidationError(
                "Unable to deserialize evidence"
            ) from exc


def _normalize_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise EvidenceValidationError("symbol must be a string")

    normalized = value.strip().upper()

    if not normalized:
        raise EvidenceValidationError("symbol cannot be blank")

    if len(normalized) > 32:
        raise EvidenceValidationError(
            "symbol cannot exceed 32 characters"
        )

    return normalized


def _normalize_source(value: str) -> str:
    if not isinstance(value, str):
        raise EvidenceValidationError("source must be a string")

    normalized = value.strip()

    if not normalized:
        raise EvidenceValidationError("source cannot be blank")

    if len(normalized) > 128:
        raise EvidenceValidationError(
            "source cannot exceed 128 characters"
        )

    return normalized


def _normalize_explanation(value: str) -> str:
    if not isinstance(value, str):
        raise EvidenceValidationError(
            "explanation must be a string"
        )

    normalized = value.strip()

    if not normalized:
        raise EvidenceValidationError(
            "explanation cannot be blank"
        )

    if len(normalized) > 4_000:
        raise EvidenceValidationError(
            "explanation cannot exceed 4000 characters"
        )

    return normalized


def _normalize_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise EvidenceValidationError(
            "timestamp must be a datetime"
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceValidationError(
            "timestamp must be timezone-aware"
        )

    return value.astimezone(timezone.utc)


def _normalize_unit_interval(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise EvidenceValidationError(
            f"{name} must be numeric"
        )

    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(
            f"{name} must be numeric"
        ) from exc

    if not math.isfinite(normalized):
        raise EvidenceValidationError(
            f"{name} must be finite"
        )

    if not 0.0 <= normalized <= 1.0:
        raise EvidenceValidationError(
            f"{name} must be between 0 and 1"
        )

    return normalized


def _freeze_mapping(
    name: str,
    value: Mapping[str, Any],
) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise EvidenceValidationError(
            f"{name} must be a mapping"
        )

    frozen: dict[str, JSONValue] = {}

    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise EvidenceValidationError(
                f"{name} keys must be nonblank strings"
            )

        frozen[key] = _freeze_value(
            item,
            path=f"{name}.{key}",
        )

    return MappingProxyType(frozen)


def _freeze_value(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceValidationError(
                f"{path} must contain only finite numbers"
            )
        return value

    if isinstance(value, Mapping):
        return _freeze_mapping(path, value)

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )

    raise EvidenceValidationError(
        f"{path} contains unsupported value type: "
        f"{type(value).__name__}"
    )


def _thaw_value(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _thaw_value(item)
            for key, item in value.items()
        }

    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]

    return value
