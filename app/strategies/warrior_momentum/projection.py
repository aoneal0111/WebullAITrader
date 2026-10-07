"""Atlas Focus projection independent of the production scanner ranking."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import MomentumCandidate


@dataclass(frozen=True, slots=True)
class AtlasFocusRow:
    rank: int
    symbol: str
    last: Decimal
    change_percent: Decimal
    relative_volume: Decimal
    float_shares: Decimal | None
    volume: Decimal
    dollar_volume: Decimal
    spread_percent: Decimal | None
    catalyst: str
    momentum_score: Decimal
    setup: str
    setup_state: str
    distance_to_hod_percent: Decimal | None
    session: str
    status: str
    explanations: tuple[str, ...]
    participation_quality: str = "UNKNOWN"
    execution_quality: str = "TEMPORARILY_BLOCKED"
    price_velocity_cents_1m: Decimal | None = None
    price_velocity_percent_1m: Decimal | None = None
    momentum_priority: Decimal = Decimal("0")


def focus_rows(candidates: tuple[MomentumCandidate, ...]) -> tuple[AtlasFocusRow, ...]:
    return tuple(
        AtlasFocusRow(
            rank=item.rank, symbol=item.symbol, last=item.price,
            change_percent=item.percentage_change, relative_volume=item.relative_volume,
            float_shares=item.float_shares, volume=item.volume,
            dollar_volume=item.dollar_volume, spread_percent=item.spread_percent,
            catalyst=item.catalyst_type.value, momentum_score=item.score.total,
            setup="--" if item.setup is None else item.setup.setup_type.value,
            setup_state="UNKNOWN" if item.setup is None else item.setup.state.value,
            distance_to_hod_percent=item.distance_from_hod_percent,
            session=item.session, status=item.status.value,
            explanations=item.explanations,
            participation_quality=item.participation_quality.value,
            execution_quality=item.execution_quality.value,
            price_velocity_cents_1m=item.price_velocity_cents_1m,
            price_velocity_percent_1m=item.price_velocity_percent_1m,
            momentum_priority=item.momentum_priority,
        ) for item in candidates
    )


def watchlist_metadata(candidate: MomentumCandidate) -> tuple[tuple[str, str], ...]:
    """Metadata names are additive and understood by the existing read model."""
    setup = candidate.setup
    return (
        ("scanner_rank", str(candidate.rank)),
        ("scanner_score", format(candidate.score.total, "f")),
        ("scanner_relative_volume", format(candidate.relative_volume, "f")),
        ("scanner_dollar_volume", format(candidate.dollar_volume, "f")),
        ("scanner_spread", "--" if candidate.spread_percent is None else format(candidate.spread_percent, "f")),
        ("scanner_rvol_band", candidate.participation_quality.value),
        ("scanner_spread_quality", candidate.execution_quality.value),
        ("scanner_price_velocity_cents_1m", "--" if candidate.price_velocity_cents_1m is None else format(candidate.price_velocity_cents_1m, "f")),
        ("scanner_price_velocity_percent_1m", "--" if candidate.price_velocity_percent_1m is None else format(candidate.price_velocity_percent_1m, "f")),
        ("scanner_momentum_priority", format(candidate.momentum_priority, "f")),
        ("scanner_reevaluation_cadence_ms", str(candidate.reevaluation_cadence_ms)),
        ("warrior_execution_block_reason", candidate.execution_block_reason or ""),
        ("scanner_catalyst", candidate.catalyst_type.value),
        ("scanner_catalyst_status", candidate.catalyst_status.value),
        ("warrior_catalyst_status", candidate.catalyst_status.value),
        ("scanner_session", candidate.session),
        ("warrior_float", "--" if candidate.float_shares is None else format(candidate.float_shares, "f")),
        ("warrior_volume", format(candidate.volume, "f")),
        ("warrior_setup", "--" if setup is None else setup.setup_type.value),
        ("warrior_setup_state", "UNKNOWN" if setup is None else setup.state.value),
        ("warrior_distance_hod", "--" if candidate.distance_from_hod_percent is None else format(candidate.distance_from_hod_percent, "f")),
        ("warrior_status", candidate.status.value),
        ("warrior_opportunity_state", candidate.opportunity_state or ""),
        ("warrior_opportunity_generation", candidate.opportunity_generation_id or ""),
        ("warrior_opportunity_reason", candidate.opportunity_reason or ""),
        ("warrior_policy_version", candidate.policy_version),
        ("warrior_discovery_status", "PASSED" if candidate.discovery_qualified else "BLOCKED"),
        ("warrior_observation_status", "ELIGIBLE" if candidate.observation_eligible else "REMOVED"),
        ("warrior_observation_blockers", " | ".join(code.value for code in candidate.observation_blockers)),
        ("warrior_entry_status", (
            candidate.opportunity_state
            or ("READY" if candidate.status.value == "ENTRY_READY" else "BLOCKED")
        )),
        ("warrior_explanations", " | ".join(candidate.explanations)),
    )


__all__ = ["AtlasFocusRow", "focus_rows", "watchlist_metadata"]
