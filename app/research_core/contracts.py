"""Immutable contracts shared by asset-specific research adapters.

These values describe evidence and hypothetical research geometry only.  They
deliberately contain no selection, authorization, sizing, broker, or execution
surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from app.assets import AssetType


_EQUITY_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,23}")
_CRYPTO_LEG = re.compile(r"[A-Z0-9]{1,16}")


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _text(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _finite(value: Decimal | None, name: str) -> None:
    if value is not None and not value.is_finite():
        raise ValueError(f"{name} must be finite when available")


@dataclass(frozen=True, slots=True)
class AssetResearchIdentity:
    """Collision-safe identity for one asset strategy lifecycle."""

    asset_type: AssetType
    canonical_symbol: str
    strategy_id: str
    strategy_version: str
    decision_cutoff: datetime
    opportunity_id: str
    lifecycle_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.asset_type, AssetType):
            raise TypeError("asset_type must be an AssetType")
        symbol = self.canonical_symbol.strip().upper()
        if self.asset_type is AssetType.EQUITY:
            if _EQUITY_SYMBOL.fullmatch(symbol) is None:
                raise ValueError("equity canonical_symbol is malformed")
        else:
            legs = symbol.split("/")
            if len(legs) != 2 or any(_CRYPTO_LEG.fullmatch(item) is None for item in legs):
                raise ValueError("crypto canonical_symbol must be a BASE/QUOTE pair")
        object.__setattr__(self, "canonical_symbol", symbol)
        for name in ("strategy_id", "strategy_version", "opportunity_id", "lifecycle_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.decision_cutoff, "decision_cutoff")

    @property
    def canonical(self) -> str:
        return json.dumps(
            {
                "asset_type": self.asset_type.value,
                "canonical_symbol": self.canonical_symbol,
                "decision_cutoff": self.decision_cutoff.isoformat(),
                "lifecycle_id": self.lifecycle_id,
                "opportunity_id": self.opportunity_id,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def deterministic_id(self) -> str:
        return sha256(("atlas-research-identity-v1|" + self.canonical).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    """Point-in-time boundary for evidence adopted by a research decision."""

    observed_at: datetime
    decision_cutoff: datetime
    evidence_timestamps: tuple[tuple[str, datetime], ...] = ()

    def __post_init__(self) -> None:
        observed = _aware(self.observed_at, "observed_at")
        cutoff = _aware(self.decision_cutoff, "decision_cutoff")
        if observed > cutoff:
            raise ValueError("observed_at cannot exceed decision_cutoff")
        names: set[str] = set()
        for raw_name, timestamp in self.evidence_timestamps:
            name = _text(raw_name, "evidence name")
            if name in names:
                raise ValueError("evidence timestamp names must be unique")
            names.add(name)
            if _aware(timestamp, f"evidence timestamp {name}") > cutoff:
                raise ValueError("future evidence exceeds decision_cutoff")


class ResearchDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class PerUnitRiskGeometry:
    """Asset-neutral hypothetical price geometry; never a sizing instruction."""

    entry_reference: Decimal
    structural_stop: Decimal
    risk_per_unit: Decimal
    direction: ResearchDirection = ResearchDirection.LONG
    target: Decimal | None = None
    r_multiple: Decimal | None = None
    mae: Decimal | None = None
    mfe: Decimal | None = None
    research_only: bool = True

    def __post_init__(self) -> None:
        for name in ("entry_reference", "structural_stop", "risk_per_unit", "target", "r_multiple", "mae", "mfe"):
            _finite(getattr(self, name), name)
        if min(self.entry_reference, self.structural_stop) <= 0 or self.risk_per_unit <= 0:
            raise ValueError("entry, stop, and risk_per_unit must be positive")
        expected = (
            self.entry_reference - self.structural_stop
            if self.direction is ResearchDirection.LONG
            else self.structural_stop - self.entry_reference
        )
        if expected <= 0 or expected != self.risk_per_unit:
            raise ValueError("risk_per_unit must equal directional entry-to-stop distance")
        if self.target is not None:
            favorable = (
                self.target - self.entry_reference
                if self.direction is ResearchDirection.LONG
                else self.entry_reference - self.target
            )
            if favorable <= 0:
                raise ValueError("target must be favorable to entry")
            if self.r_multiple is not None and favorable / self.risk_per_unit != self.r_multiple:
                raise ValueError("r_multiple must match target geometry")
        elif self.r_multiple is not None:
            raise ValueError("r_multiple requires a target")
        if not self.research_only:
            raise ValueError("risk geometry must remain research-only")


class EvidenceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """Transparent score evidence; unavailable is distinct from numeric zero."""

    name: str
    value: Decimal | None
    availability: EvidenceAvailability
    version: str
    weight: Decimal | None = None
    provenance: EvidenceProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "score component name"))
        object.__setattr__(self, "version", _text(self.version, "score component version"))
        _finite(self.value, "score component value")
        _finite(self.weight, "score component weight")
        if (self.value is not None) != (self.availability is EvidenceAvailability.AVAILABLE):
            raise ValueError("score value must exactly match availability")
        if self.weight is not None and self.weight < 0:
            raise ValueError("score component weight cannot be negative")


@dataclass(frozen=True, slots=True)
class ResearchDetection:
    identity: AssetResearchIdentity
    family: str
    state: str
    reference_price: Decimal | None
    trigger_level: Decimal | None
    structural_stop: Decimal | None
    quality_components: tuple[tuple[str, Decimal], ...]
    observed_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    reasons: tuple[str, ...]
    provenance: EvidenceProvenance
    research_only: bool = True

    def __post_init__(self) -> None:
        for name in ("family", "state"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("reference_price", "trigger_level", "structural_stop"):
            _finite(getattr(self, name), name)
        if self.provenance.decision_cutoff != self.identity.decision_cutoff:
            raise ValueError("detection provenance cutoff must match identity cutoff")
        if not self.research_only:
            raise ValueError("strategy detection must remain research-only")


class OpportunityLifecycleState(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True, slots=True)
class OpportunityLifecycle:
    """Semantic episode state, independent of any working-order lifecycle."""

    identity: AssetResearchIdentity
    state: OpportunityLifecycleState
    semantic_identity: str
    invalidation_reason: str | None = None
    research_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_identity", _text(self.semantic_identity, "semantic_identity")
        )
        if (self.state is OpportunityLifecycleState.INVALIDATED) != (
            self.invalidation_reason is not None
        ):
            raise ValueError("invalidation reason must exactly match invalidated state")
        if self.invalidation_reason is not None:
            object.__setattr__(
                self,
                "invalidation_reason",
                _text(self.invalidation_reason, "invalidation_reason"),
            )
        if not self.research_only:
            raise ValueError("opportunity lifecycle must remain research-only")

    @property
    def terminal(self) -> bool:
        return self.state in {
            OpportunityLifecycleState.INVALIDATED,
            OpportunityLifecycleState.TERMINAL,
        }

    def is_material_change_from(self, prior: OpportunityLifecycle) -> bool:
        """Compare semantic state only; quote noise is excluded by the adapter."""

        return (
            self.identity.asset_type is not prior.identity.asset_type
            or self.identity.canonical_symbol != prior.identity.canonical_symbol
            or self.identity.strategy_id != prior.identity.strategy_id
            or self.identity.strategy_version != prior.identity.strategy_version
            or self.identity.opportunity_id != prior.identity.opportunity_id
            or self.identity.lifecycle_id != prior.identity.lifecycle_id
            or self.state is not prior.state
            or self.semantic_identity != prior.semantic_identity
            or self.invalidation_reason != prior.invalidation_reason
        )


@dataclass(frozen=True, slots=True)
class ResearchOpportunity:
    identity: AssetResearchIdentity
    primary_strategy_id: str
    strategy_memberships: tuple[str, ...]
    reference_price: Decimal | None
    structural_stop: Decimal | None
    complete_r_plan: bool
    research_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_strategy_id", _text(self.primary_strategy_id, "primary_strategy_id"))
        if not self.strategy_memberships or len(set(self.strategy_memberships)) != len(self.strategy_memberships):
            raise ValueError("strategy memberships must be non-empty and unique")
        if not self.research_only:
            raise ValueError("opportunity must remain research-only")


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    identity: AssetResearchIdentity
    reference_price: Decimal
    structural_stop: Decimal | None
    horizon_seconds: int
    target_timestamp: datetime
    observed_at: datetime
    return_percent: Decimal | None
    mae: Decimal | None
    mfe: Decimal | None
    mae_r: Decimal | None
    mfe_r: Decimal | None
    evidence_timestamp: datetime
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.reference_price <= 0 or self.horizon_seconds <= 0:
            raise ValueError("outcome reference and horizon must be positive")
        for name in ("structural_stop", "return_percent", "mae", "mfe", "mae_r", "mfe_r"):
            _finite(getattr(self, name), name)
        target = _aware(self.target_timestamp, "target_timestamp")
        observed = _aware(self.observed_at, "observed_at")
        evidence = _aware(self.evidence_timestamp, "evidence_timestamp")
        if target <= self.identity.decision_cutoff or observed < target or evidence > observed:
            raise ValueError("outcome timestamps violate the forward-evidence boundary")
        if not self.research_only:
            raise ValueError("outcome must remain research-only")
