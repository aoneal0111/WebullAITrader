"""Explainable historical analog retrieval using decision-time fields only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from .experience_store import ExperienceStore
from .models import (
    HorizonOutcome, OutcomeStatus, TradeOpportunityExperience,
    decision_analog_signature,
)


@dataclass(frozen=True, slots=True)
class AnalogQuery:
    target: TradeOpportunityExperience
    as_of: datetime
    minimum_sample_size: int = 20
    limit: int = 200


@dataclass(frozen=True, slots=True)
class AnalogResult:
    experience_ids: tuple[str, ...]
    matched_dimensions: tuple[str, ...]
    sample_size: int
    evidence_sufficient: bool
    reached_1r_rate: Decimal | None
    reached_2r_rate: Decimal | None
    stop_first_rate: Decimal | None
    median_mfe_r: Decimal | None
    median_mae_r: Decimal | None


class HistoricalAnalogEngine:
    """Exact categorical/bucket matching; no opaque weighted distance."""

    DIMENSIONS = (
        "price_bucket", "change_bucket", "rvol_bucket", "float_bucket",
        "spread_bucket", "setup_type", "session", "catalyst_status",
        "pullback_bucket", "distance_hod_bucket", "setup_state",
    )

    def __init__(self, store: ExperienceStore) -> None:
        self.store = store

    def query(self, query: AnalogQuery) -> AnalogResult:
        if query.as_of.tzinfo is None or query.minimum_sample_size <= 0 or query.limit <= 0:
            raise ValueError("valid aware as_of and positive query limits are required")
        matches = self.store.analog_experiences(
            decision_analog_signature(query.target), query.as_of, query.limit,
        )
        outcomes = []
        grouped_outcomes = self.store.outcomes_for_experiences(
            tuple(exp.experience_id for exp in matches)
        )
        for exp in matches:
            complete = [item for item in grouped_outcomes[exp.experience_id] if item.status is OutcomeStatus.COMPLETE]
            if complete:
                outcomes.append(max(complete, key=lambda item: item.horizon_minutes))
        sufficient = len(outcomes) >= query.minimum_sample_size
        return AnalogResult(
            tuple(item.experience_id for item in matches), self.DIMENSIONS,
            len(outcomes), sufficient,
            _rate(outcomes, "reached_1r") if sufficient else None,
            _rate(outcomes, "reached_2r") if sufficient else None,
            _first_rate(outcomes, "STOP") if sufficient else None,
            _median(outcomes, "mfe_r") if sufficient else None,
            _median(outcomes, "mae_r") if sufficient else None,
        )


def _rate(values, field):
    known = [getattr(item, field) for item in values if getattr(item, field) is not None]
    return None if not known else Decimal(sum(bool(item) for item in known)) / Decimal(len(known))


def _first_rate(values, event):
    known = [item.first_plan_event for item in values if item.first_plan_event is not None]
    return None if not known else Decimal(sum(item == event for item in known)) / Decimal(len(known))


def _median(values, field):
    known = [getattr(item, field) for item in values if getattr(item, field) is not None]
    return None if not known else Decimal(median(known))
