from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.assets import AssetType
from app.research_core import (
    AssetResearchIdentity,
    DetectorRegistry,
    EvidenceAvailability,
    EvidenceProvenance,
    OpportunityLifecycle,
    OpportunityLifecycleState,
    PerUnitRiskGeometry,
    ResearchDetection,
    ResearchDirection,
    ResearchOutcome,
    ScoreComponent,
    remember_bounded,
    semantic_digest,
)
from app.trade_intelligence.models import DecisionTimeSnapshot


CUTOFF = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)


def crypto_identity() -> AssetResearchIdentity:
    return AssetResearchIdentity(
        asset_type=AssetType.CRYPTO,
        canonical_symbol="btc/usd",
        strategy_id="FUTURE_CRYPTO_RESEARCH",
        strategy_version="V1",
        decision_cutoff=CUTOFF,
        opportunity_id="opportunity-1",
        lifecycle_id="episode-1",
    )


def test_crypto_identity_is_canonical_collision_safe_and_session_neutral():
    identity = crypto_identity()
    assert identity.canonical_symbol == "BTC/USD"
    assert identity.deterministic_id == crypto_identity().deterministic_id
    assert "session" not in {item.name for item in fields(AssetResearchIdentity)}
    assert "session_date" not in {item.name for item in fields(AssetResearchIdentity)}

    equity = AssetResearchIdentity(
        AssetType.EQUITY,
        "BTC",
        identity.strategy_id,
        identity.strategy_version,
        identity.decision_cutoff,
        identity.opportunity_id,
        identity.lifecycle_id,
    )
    assert equity.deterministic_id != identity.deterministic_id
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        AssetResearchIdentity(
            AssetType.CRYPTO, "BTC", "S", "V1", CUTOFF, "o", "e"
        )


def test_provenance_rejects_future_or_ambiguous_evidence():
    provenance = EvidenceProvenance(
        observed_at=CUTOFF,
        decision_cutoff=CUTOFF,
        evidence_timestamps=(("bar", CUTOFF - timedelta(seconds=1)),),
    )
    assert provenance.evidence_timestamps[0][1] <= provenance.decision_cutoff

    with pytest.raises(ValueError, match="future evidence"):
        EvidenceProvenance(
            CUTOFF,
            CUTOFF,
            (("bar", CUTOFF + timedelta(microseconds=1)),),
        )
    with pytest.raises(ValueError, match="unique"):
        EvidenceProvenance(CUTOFF, CUTOFF, (("bar", CUTOFF), ("bar", CUTOFF)))


def test_per_unit_geometry_supports_crypto_without_sizing_or_authority():
    geometry = PerUnitRiskGeometry(
        entry_reference=Decimal("100"),
        structural_stop=Decimal("95"),
        risk_per_unit=Decimal("5"),
        target=Decimal("110"),
        r_multiple=Decimal("2"),
        mae=Decimal("1.5"),
        mfe=Decimal("7"),
    )
    assert geometry.risk_per_unit == Decimal("5")
    assert geometry.research_only
    assert not ({"quantity", "buying_power", "authorize", "execute"} &
                {item.name for item in fields(PerUnitRiskGeometry)})

    short_geometry = PerUnitRiskGeometry(
        Decimal("100"), Decimal("105"), Decimal("5"), ResearchDirection.SHORT
    )
    assert short_geometry.direction is ResearchDirection.SHORT


def test_equity_per_share_snapshot_has_an_exact_per_unit_compatibility_view():
    snapshot = DecisionTimeSnapshot(
        decision_timestamp=CUTOFF,
        trigger_price=Decimal("10.50"),
        structural_stop=Decimal("10.00"),
        risk_per_share=Decimal("0.50"),
    )
    geometry = snapshot.per_unit_risk_geometry
    assert geometry is not None
    assert geometry.entry_reference == snapshot.trigger_price
    assert geometry.structural_stop == snapshot.structural_stop
    assert geometry.risk_per_unit == snapshot.risk_per_share


def test_score_component_preserves_unavailable_distinct_from_zero():
    unavailable = ScoreComponent(
        "depth_pressure", None, EvidenceAvailability.UNAVAILABLE, "V1"
    )
    zero = ScoreComponent(
        "relative_strength", Decimal("0"), EvidenceAvailability.AVAILABLE, "V1"
    )
    assert unavailable.value is None
    assert zero.value == 0
    with pytest.raises(ValueError, match="availability"):
        ScoreComponent("invalid", None, EvidenceAvailability.AVAILABLE, "V1")


def test_detection_and_outcome_contracts_are_research_only():
    identity = crypto_identity()
    provenance = EvidenceProvenance(CUTOFF, CUTOFF)
    detection = ResearchDetection(
        identity=identity,
        family="CONTINUATION",
        state="DETECTED",
        reference_price=Decimal("100"),
        trigger_level=Decimal("101"),
        structural_stop=Decimal("98"),
        quality_components=(("momentum", Decimal("1")),),
        observed_evidence=("rolling_bars",),
        missing_evidence=(),
        reasons=("TEST_EVIDENCE",),
        provenance=provenance,
    )
    outcome = ResearchOutcome(
        identity=identity,
        reference_price=Decimal("100"),
        structural_stop=Decimal("98"),
        horizon_seconds=60,
        target_timestamp=CUTOFF + timedelta(seconds=60),
        observed_at=CUTOFF + timedelta(seconds=61),
        return_percent=Decimal("1"),
        mae=Decimal("0.5"),
        mfe=Decimal("1.2"),
        mae_r=Decimal("0.25"),
        mfe_r=Decimal("0.6"),
        evidence_timestamp=CUTOFF + timedelta(seconds=60),
    )
    assert detection.research_only and outcome.research_only
    assert "selection_authorized" not in {item.name for item in fields(ResearchDetection)}


@dataclass(frozen=True)
class _Definition:
    strategy_id: str
    research_only: bool = True


@dataclass(frozen=True)
class _Detector:
    definition: _Definition

    def detect(self, context: str) -> str:
        return f"{self.definition.strategy_id}:{context}"


def test_generic_detector_registry_is_ordered_static_and_research_only():
    registry = DetectorRegistry((_Detector(_Definition("B")), _Detector(_Definition("A"))))
    assert registry.evaluate("context") == ("B:context", "A:context")
    assert tuple(item.definition.strategy_id for item in registry.detectors) == ("B", "A")
    with pytest.raises(ValueError, match="unique"):
        DetectorRegistry((_Detector(_Definition("A")), _Detector(_Definition("A"))))
    with pytest.raises(ValueError, match="research-only"):
        DetectorRegistry((_Detector(_Definition("A", False)),))
    with pytest.raises(ValueError, match="explicit bound"):
        DetectorRegistry((_Detector(_Definition("A")),), maximum_detectors=0)


def test_bounded_lifecycle_helper_evicts_least_recent_semantic_state():
    retained: OrderedDict[str, int] = OrderedDict()
    remember_bounded(retained, "a", 1, limit=2)
    remember_bounded(retained, "b", 2, limit=2)
    remember_bounded(retained, "a", 3, limit=2)
    remember_bounded(retained, "c", 4, limit=2)
    assert tuple(retained.items()) == (("a", 3), ("c", 4))
    with pytest.raises(ValueError, match="positive"):
        remember_bounded(retained, "d", 5, limit=0)


def test_opportunity_lifecycle_has_explicit_material_and_terminal_boundaries():
    identity = crypto_identity()
    forming = OpportunityLifecycle(
        identity,
        OpportunityLifecycleState.CANDIDATE,
        semantic_digest("forming", "level-1"),
    )
    same = OpportunityLifecycle(
        identity,
        OpportunityLifecycleState.CANDIDATE,
        semantic_digest("forming", "level-1"),
    )
    invalidated = OpportunityLifecycle(
        identity,
        OpportunityLifecycleState.INVALIDATED,
        semantic_digest("invalidated", "level-1"),
        "STRUCTURE_FAILED",
    )
    assert not same.is_material_change_from(forming)
    assert invalidated.is_material_change_from(forming)
    assert not forming.terminal and invalidated.terminal
    with pytest.raises(ValueError, match="invalidation reason"):
        OpportunityLifecycle(
            identity,
            OpportunityLifecycleState.INVALIDATED,
            semantic_digest("invalid"),
        )
