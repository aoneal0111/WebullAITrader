"""Bounded top-of-book execution pursuit for already-authorized entries.

This module deliberately does not form or change a Warrior trade thesis.  It
only evaluates whether a working LIMIT entry has enough current top-of-book
evidence to request the existing, bounded cancel/replace path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.market_data.models import BookLevel


class ExecutionPursuitDecision(StrEnum):
    HOLD_PASSIVE = "HOLD_PASSIVE"
    IMPROVE_LIMIT = "IMPROVE_LIMIT"
    PURSUE_ONE_LEVEL = "PURSUE_ONE_LEVEL"
    ABANDON = "ABANDON"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ExecutionPursuitAssessment:
    decision: ExecutionPursuitDecision
    evaluated_at: datetime
    reason: str
    working_limit: Decimal | None = None
    proposed_limit: Decimal | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread_percent: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    advancing_pressure: bool = False
    data_mode: str = "TOP_OF_BOOK"
    depth_imbalance: Decimal | None = None
    near_touch_bid_depth: Decimal | None = None
    near_touch_ask_depth: Decimal | None = None
    microprice: Decimal | None = None
    ask_state: str | None = None


@dataclass(frozen=True, slots=True)
class DepthFeatures:
    bid_depth: Decimal
    ask_depth: Decimal
    near_touch_bid_depth: Decimal
    near_touch_ask_depth: Decimal
    imbalance: Decimal | None
    microprice: Decimal | None


def depth_features(
    bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...], *, near_levels: int = 3,
) -> DepthFeatures | None:
    """Calculate bounded depth features from one validated snapshot."""
    if not bids or not asks or near_levels <= 0:
        return None
    bid_depth = sum((level.size for level in bids), Decimal("0"))
    ask_depth = sum((level.size for level in asks), Decimal("0"))
    near_bid = sum((level.size for level in bids[:near_levels]), Decimal("0"))
    near_ask = sum((level.size for level in asks[:near_levels]), Decimal("0"))
    total = bid_depth + ask_depth
    imbalance = None if total <= 0 else (bid_depth - ask_depth) / total
    touch_total = bids[0].size + asks[0].size
    microprice = (
        None if touch_total <= 0
        else (asks[0].price * bids[0].size + bids[0].price * asks[0].size) / touch_total
    )
    return DepthFeatures(bid_depth, ask_depth, near_bid, near_ask, imbalance, microprice)


def depth_transition(
    previous_bids: tuple[BookLevel, ...] | None,
    previous_asks: tuple[BookLevel, ...] | None,
    current_bids: tuple[BookLevel, ...],
    current_asks: tuple[BookLevel, ...],
) -> tuple[str, bool]:
    """Return cautious ask depletion/replenishment and bid advancement signals."""
    if not previous_bids or not previous_asks or not current_bids or not current_asks:
        return "UNKNOWN", False
    previous_ask = previous_asks[0]
    current_ask = current_asks[0]
    if current_ask.price > previous_ask.price or (
        current_ask.price == previous_ask.price and current_ask.size < previous_ask.size
    ):
        ask_state = "DEPTH_DEPLETION"
    elif current_ask.price == previous_ask.price and current_ask.size > previous_ask.size:
        ask_state = "DEPTH_REPLENISHMENT"
    else:
        ask_state = "UNCHANGED"
    return ask_state, current_bids[0].price > previous_bids[0].price


def assess_top_of_book_pursuit(
    *,
    evaluated_at: datetime,
    working_limit: Decimal,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
    bid_size: Decimal | None,
    ask_size: Decimal | None,
    spread_percent: Decimal | None,
    maximum_spread_percent: Decimal,
    quote_fresh: bool,
    liquidity_ok: bool,
    thesis_valid: bool,
    quality_ok: bool,
    replacement_budget_available: bool,
    structural_stop: Decimal,
    expected_reward: Decimal | None = None,
    depth: DepthFeatures | None = None,
    ask_state: str | None = None,
    bid_advancing: bool = False,
) -> ExecutionPursuitAssessment:
    """Assess a single bounded pursuit opportunity from top-of-book data.

    Missing size is intentionally conservative: without size evidence this
    fallback does not claim order-flow pressure.  Existing legacy adaptive
    behavior remains available to callers that have no size data at all.
    """
    def blocked(reason: str) -> ExecutionPursuitAssessment:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.BLOCKED, evaluated_at, reason,
            working_limit=working_limit, best_bid=best_bid, best_ask=best_ask,
            spread_percent=spread_percent, bid_size=bid_size, ask_size=ask_size,
            depth_imbalance=None if depth is None else depth.imbalance,
            near_touch_bid_depth=None if depth is None else depth.near_touch_bid_depth,
            near_touch_ask_depth=None if depth is None else depth.near_touch_ask_depth,
            microprice=None if depth is None else depth.microprice, ask_state=ask_state,
        )

    if not quote_fresh:
        return blocked("STALE_QUOTE")
    if not thesis_valid:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.ABANDON, evaluated_at, "THESIS_INVALID",
            working_limit=working_limit, best_bid=best_bid, best_ask=best_ask,
            spread_percent=spread_percent, bid_size=bid_size, ask_size=ask_size,
        )
    if not quality_ok:
        return blocked("OPPORTUNITY_QUALITY_BLOCKED")
    if not liquidity_ok:
        return blocked("LIQUIDITY_BLOCKED")
    if best_bid is None or best_ask is None or spread_percent is None:
        return blocked("TOP_OF_BOOK_UNAVAILABLE")
    if spread_percent > maximum_spread_percent:
        return blocked("SPREAD_WIDE")
    if structural_stop <= 0 or working_limit <= structural_stop:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.ABANDON, evaluated_at,
            "STRUCTURAL_STOP_INVALID", working_limit=working_limit,
            best_bid=best_bid, best_ask=best_ask, spread_percent=spread_percent,
            bid_size=bid_size, ask_size=ask_size,
        )
    if expected_reward is not None and expected_reward <= best_ask - structural_stop:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.ABANDON, evaluated_at,
            "REWARD_RISK_UNACCEPTABLE", working_limit=working_limit,
            best_bid=best_bid, best_ask=best_ask, spread_percent=spread_percent,
            bid_size=bid_size, ask_size=ask_size,
        )
    if best_ask <= working_limit:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.HOLD_PASSIVE, evaluated_at,
            "ASK_NOT_ADVANCING", working_limit=working_limit,
            best_bid=best_bid, best_ask=best_ask, spread_percent=spread_percent,
            bid_size=bid_size, ask_size=ask_size,
        )
    if bid_size is None or ask_size is None:
        return blocked("ORDER_FLOW_SIZE_UNAVAILABLE")
    advancing = bid_size >= ask_size and best_bid >= working_limit
    if depth is not None:
        advancing = bool(
            depth.imbalance is not None and depth.imbalance > Decimal("0")
            and (bid_advancing or ask_state == "DEPTH_DEPLETION")
        )
    if not advancing:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.HOLD_PASSIVE, evaluated_at,
            "ADVANCING_PRESSURE_NOT_CONFIRMED", working_limit=working_limit,
            proposed_limit=best_ask, best_bid=best_bid, best_ask=best_ask,
            spread_percent=spread_percent, bid_size=bid_size, ask_size=ask_size,
            depth_imbalance=None if depth is None else depth.imbalance,
            near_touch_bid_depth=None if depth is None else depth.near_touch_bid_depth,
            near_touch_ask_depth=None if depth is None else depth.near_touch_ask_depth,
            microprice=None if depth is None else depth.microprice, ask_state=ask_state,
        )
    if not replacement_budget_available:
        return ExecutionPursuitAssessment(
            ExecutionPursuitDecision.ABANDON, evaluated_at,
            "PURSUIT_BUDGET_EXHAUSTED", working_limit=working_limit,
            proposed_limit=best_ask, best_bid=best_bid, best_ask=best_ask,
            spread_percent=spread_percent, bid_size=bid_size, ask_size=ask_size,
            advancing_pressure=True,
        )
    return ExecutionPursuitAssessment(
        ExecutionPursuitDecision.PURSUE_ONE_LEVEL, evaluated_at, "ADVANCING_TOP_OF_BOOK",
        working_limit=working_limit, proposed_limit=best_ask,
        best_bid=best_bid, best_ask=best_ask, spread_percent=spread_percent,
        bid_size=bid_size, ask_size=ask_size, advancing_pressure=True,
        depth_imbalance=None if depth is None else depth.imbalance,
        near_touch_bid_depth=None if depth is None else depth.near_touch_bid_depth,
        near_touch_ask_depth=None if depth is None else depth.near_touch_ask_depth,
        microprice=None if depth is None else depth.microprice, ask_state=ask_state,
    )


__all__ = [
    "DepthFeatures", "ExecutionPursuitAssessment", "ExecutionPursuitDecision",
    "assess_top_of_book_pursuit", "depth_features", "depth_transition",
]
