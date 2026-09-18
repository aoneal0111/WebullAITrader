from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .forward_models import CaptureMetrics, FloatProvenance
from .models import MomentumCandidate


@dataclass(frozen=True, slots=True)
class WarriorPaperSummary:
    discovered: int = 0
    stocks_in_play: int = 0
    near: int = 0
    qualified: int = 0
    setup_forming: int = 0
    triggered: int = 0
    entry_ready: int = 0
    open_paper_trades: int = 0
    today_paper_r: Decimal | None = None
    today_trades: int = 0
    triggered_but_blocked: int = 0
    tracked_counterfactuals: int = 0


@dataclass(frozen=True, slots=True)
class WarriorFocusItem:
    candidate: MomentumCandidate
    float_provenance: FloatProvenance
    entry_trigger: Decimal | None
    stop_price: Decimal | None
    blocking_reasons: tuple[str, ...]
    market_data_stale: bool = False
    market_data_age_seconds: Decimal | None = None
    decision_timestamp: datetime | None = None
    decision_last: Decimal | None = None
    decision_bid: Decimal | None = None
    decision_ask: Decimal | None = None
    decision_spread_percent: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WarriorPaperSnapshot:
    enabled: bool
    health: object
    configuration_fingerprint: str
    items: tuple[WarriorFocusItem, ...] = ()
    summary: WarriorPaperSummary = WarriorPaperSummary()
    metrics: CaptureMetrics | None = None
    last_error_type: str | None = None
    publication_rate_hz: Decimal = Decimal("0")


def blocking_reasons(
    candidate: MomentumCandidate,
    entry_ready: bool,
) -> tuple[str, ...]:
    if entry_ready:
        return ()
    mapping = {
        "PRICE_TOO_LOW": "price",
        "PRICE_TOO_HIGH": "price",
        "CHANGE_TOO_LOW": "change",
        "RVOL_LOW": "scanner_rvol",
        "FLOAT_HIGH": "float",
        "SPREAD_WIDE": "spread",
        "LIQUIDITY_LOW": "participation",
        "HALTED": "halt",
        "HALT_UNKNOWN": "halt",
        "NOT_TRADABLE": "tradability",
        "SESSION_NOT_ALLOWED": "session",
        "STOP_TOO_WIDE": "risk",
        "STOP_INVALID": "risk",
        "RISK_REJECTED": "strategy_eligibility",
        "NO_SETUP": "setup",
        "STALE_MARKET_DATA": "stale_market_data",
        "AWAITING_EXECUTION_QUOTE": "awaiting_execution_quote",
    }
    return tuple(
        dict.fromkeys(
            mapping[code.value]
            for code in candidate.reason_codes
            if code.value in mapping
        )
    )


def scanner_classification(decision: object, ranked: bool) -> str | None:
    """Mirror scanner projection labels without creating trading authority."""

    if ranked:
        return "QUALIFYING"
    if bool(
        getattr(
            decision,
            "technical_qualifies_without_catalyst",
            False,
        )
    ):
        return "WATCHING"
    failed = tuple(getattr(decision, "technical_failed_rules", ()))
    if len(failed) == 1 and failed[0] in {
        "price_range",
        "percentage_change",
        "relative_volume",
        "low_float",
        "dollar_volume",
        "spread",
    }:
        return "NEAR MISS"
    return None


__all__ = [
    "WarriorFocusItem",
    "WarriorPaperSnapshot",
    "WarriorPaperSummary",
    "blocking_reasons",
    "scanner_classification",
]
