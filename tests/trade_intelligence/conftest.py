from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.trade_intelligence.models import (
    FEATURE_VERSION, AtlasDecision, DecisionTimeSnapshot, OpportunityKey,
    TradeOpportunityExperience,
)

T0 = datetime(2026, 8, 31, 14, 30, tzinfo=UTC)


def make_experience(
    symbol="ABCD", episode="setup-1", decision=AtlasDecision.REJECTED,
    blockers=("NO_CATALYST",), at=T0, traded=False,
):
    return TradeOpportunityExperience(
        key=OpportunityKey("WARRIOR_MOMENTUM_V1", symbol, at.date(), "REGULAR", episode),
        environment="TEST", policy_version="CONSERVATIVE_V1",
        strategy_version="WARRIOR_MOMENTUM_V1", model_version="NONE",
        feature_version=FEATURE_VERSION, source_event_identity=f"source:{episode}",
        snapshot=DecisionTimeSnapshot(
            decision_timestamp=at, source_timestamp=at,
            last_price=Decimal("10"), bid=Decimal("9.99"), ask=Decimal("10.01"),
            spread_percent=Decimal("0.2"), percentage_change=Decimal("25"),
            current_volume=Decimal("1000000"), average_volume=Decimal("100000"),
            relative_volume=Decimal("10"), dollar_volume=Decimal("10000000"),
            float_shares=Decimal("5000000"), tradable=True, halted=False,
            quote_freshness_seconds=Decimal("0.1"), trade_freshness_seconds=Decimal("0.1"),
            catalyst_status="FALSE", catalyst_type="NONE", scanner_qualified=True,
            scanner_score=Decimal("90"), scanner_rank=1,
            passed_rules=("price", "rvol"), failed_rules=("catalyst",),
            setup_state="TRIGGERED", setup_type="MICRO_PULLBACK",
            setup_quality=Decimal("80"), trigger_price=Decimal("10"),
            structural_stop=Decimal("9.5"), reference_price=Decimal("10"),
            risk_per_share=Decimal("0.5"), setup_timestamp=at,
            completed_bar_identity="bar:prior",
            features=(
                ("pullback_depth_percent", Decimal("2")),
                ("distance_from_hod_percent", Decimal("-1")),
            ),
            feature_source_timestamps=(("pullback_depth_percent", at), ("distance_from_hod_percent", at)),
        ),
        atlas_decision=decision, blockers=blockers,
        technically_actionable=True, actually_traded=traded,
    )


@pytest.fixture
def experience():
    return make_experience()
