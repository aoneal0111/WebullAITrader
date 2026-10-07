"""Coalesced Warrior paper read-model formatting for Mission Control."""

from __future__ import annotations

from dataclasses import dataclass

from app.gui.formatters.prices import format_price
from app.gui.models.watchlist import WatchlistRow, WatchlistSnapshot
from app.strategies.warrior_momentum.desktop_sidecar import WarriorPaperSnapshot


@dataclass(frozen=True, slots=True)
class WarriorPaperView:
    focus: WatchlistSnapshot
    summary: str
    funnel: str
    research: str
    enabled: bool
    health: str
    observability_health: str = "DISABLED"
    entry_authorized: bool = False
    last_observation: str = "--"
    last_full_evaluation: str = "--"
    last_health_reason: str = "--"


def format_warrior_paper(snapshot: WarriorPaperSnapshot) -> WarriorPaperView:
    rows = tuple(_row(item) for item in snapshot.items)
    summary = snapshot.summary
    paper_r = "N/A" if summary.today_paper_r is None else f"{summary.today_paper_r:+.2f}R"
    return WarriorPaperView(
        WatchlistSnapshot(
            rows=rows, scanner_status=snapshot.health.value,
            candidate_count=len(rows), empty_title="Warrior Paper is observing",
            empty_detail="No point-in-time candidates are available yet.",
        ),
        f"Today: {summary.today_trades} trades · {paper_r} · "
        f"Open: {summary.open_paper_trades}",
        " → ".join((
            f"D {summary.discovered}", f"SIP {summary.stocks_in_play}",
            f"N {summary.near}", f"Q {summary.qualified}",
            f"Setup {summary.setup_forming}", f"Trig {summary.triggered}",
            f"Ready {summary.entry_ready}",
            f"Paper {summary.today_trades}",
        )),
        f"Triggered but blocked: {summary.triggered_but_blocked} · "
        f"Tracked counterfactuals: {summary.tracked_counterfactuals}",
        snapshot.enabled,
        snapshot.health.value,
        snapshot.observability_health.value,
        snapshot.entry_authorized,
        (
            "--" if snapshot.last_observation_at is None
            else snapshot.last_observation_at.isoformat()
        ),
        (
            "--" if snapshot.last_full_evaluation_at is None
            else snapshot.last_full_evaluation_at.isoformat()
        ),
        (
            "--" if snapshot.last_health_transition is None
            else (
                f"{snapshot.last_health_transition.category}:"
                f"{snapshot.last_health_transition.reason}"
            )
        ),
    )


def _row(item) -> WatchlistRow:
    candidate = item.candidate
    setup = candidate.setup
    generation_mismatch = bool(
        setup is not None
        and candidate.opportunity_generation_id
        and item.decision_generation_id
        and candidate.opportunity_generation_id != item.decision_generation_id
    )
    if generation_mismatch:
        setup = None
    # Candidate reason codes include non-blocking quality facts (for example
    # catalyst NONE under Balanced V1).  The sidecar's entry blockers are the
    # authoritative presentation source.
    raw_blockers = tuple(dict.fromkeys((
        *item.blocking_reasons,
        *((candidate.opportunity_reason,) if candidate.opportunity_reason else ()),
        *(("OPPORTUNITY_GENERATION_CHANGED",) if generation_mismatch else ()),
    )))
    scanner_notes = tuple(reason for reason in raw_blockers if reason == "scanner_rvol")
    execution_blockers = tuple(reason for reason in raw_blockers if reason != "scanner_rvol")
    readable_blockers = tuple(dict.fromkeys(
        _readable_blocker(reason) for reason in execution_blockers
    ))
    blocking = "\n".join(readable_blockers)
    if not blocking and (candidate.opportunity_state or "") in {
        "WAITING_EXECUTION", "REJECTED_HARD_SAFETY", "INVALIDATED", "EXPIRED",
    }:
        blocking = "Opportunity state changed; awaiting a generation-bound decision"
    blocking = blocking or "--"
    scalp_state = item.quick_scalper_state or "--"
    scalp_reason = item.quick_scalper_reason or "--"
    scalp_generation = item.quick_scalper_generation_id or "--"
    scalp_visible = scalp_generation != "--"
    explanations = (*candidate.explanations, *(
        (_readable_blocker(reason) for reason in scanner_notes)
    ), *(
        (f"Blocked: {blocking}",) if blocking != "--" else ()
    ), *(
        (
            f"Quick Scalper {scalp_state.replace('_', ' ')}: "
            f"{_readable_blocker(scalp_reason)} "
            f"(generation {scalp_generation})",
        ) if scalp_visible else ()
    ))
    presented_status = candidate.opportunity_state or candidate.status.value
    return WatchlistRow(
        symbol=candidate.symbol, selected=False,
        latest_price=format_price(candidate.price), change="--",
        change_percent=f"{candidate.percentage_change:+.2f}%",
        bid="--", ask="--", volume=f"{candidate.volume:,.0f}",
        market_status=candidate.session, last_update=candidate.timestamp.isoformat(),
        stale=("STALE" if item.market_data_stale else "LIVE"),
        rank=str(candidate.rank), score=f"{candidate.score.total:.2f}",
        relative_volume=f"{candidate.relative_volume:.2f}x",
        dollar_volume=f"${candidate.dollar_volume:,.0f}",
        spread="--" if candidate.spread_percent is None else f"{candidate.spread_percent:.2f}%",
        catalyst=_catalyst_label(candidate), session=candidate.session,
        float_shares=("--" if candidate.float_shares is None else f"{candidate.float_shares / 1_000_000:.1f}M"),
        setup="NO SETUP" if setup is None else setup.setup_type.value.replace("_", " "),
        setup_state="--" if setup is None else setup.state.value,
        distance_to_hod=("--" if candidate.distance_from_hod_percent is None else f"{candidate.distance_from_hod_percent:.2f}%"),
        strategy_status=("ENTRY BLOCKED" if blocking != "--" and setup is not None and setup.state.value == "TRIGGERED" else presented_status.replace("_", " ")),
        explanations=" | ".join(explanations),
        float_provenance=item.float_provenance.value.replace("MARKET_CAP_PRICE_PROXY", "MCAP/PRICE PROXY"),
        entry_trigger=format_price(item.entry_trigger),
        stop_price=format_price(item.stop_price),
        blocking_reasons=blocking,
        warrior_evaluated=True,
        warrior_score=f"{candidate.score.total:.2f}",
        warrior_status=presented_status.replace("_", " "),
        warrior_session=candidate.session,
        strategy_name=(
            "Warrior Momentum + Quick Scalper"
            if scalp_visible else "Warrior Momentum"
        ),
        market_timestamp=(
            candidate.timestamp.isoformat()
            if item.current_quote_timestamp is None
            else item.current_quote_timestamp.isoformat()
        ),
        decision_timestamp=(
            "--" if item.decision_timestamp is None
            else item.decision_timestamp.isoformat()
        ),
        decision_last=format_price(item.decision_last),
        decision_bid=format_price(item.decision_bid),
        decision_ask=format_price(item.decision_ask),
        decision_spread=(
            "--" if item.decision_spread_percent is None
            else f"{item.decision_spread_percent:.2f}%"
        ),
        decision_generation_id=item.decision_generation_id or "--",
        scanner_observation_timestamp=(
            "--" if item.scanner_observation_timestamp is None
            else item.scanner_observation_timestamp.isoformat()
        ),
        warrior_observation_timestamp=(
            "--" if item.warrior_observation_timestamp is None
            else item.warrior_observation_timestamp.isoformat()
        ),
        decision_quote_timestamp=(
            "--" if item.decision_quote_timestamp is None
            else item.decision_quote_timestamp.isoformat()
        ),
        execution_quality=item.execution_quality,
        scalper_state=scalp_state.replace("_", " "),
        scalper_reason=(
            "--" if scalp_reason == "--" else _readable_blocker(scalp_reason)
        ),
        scalper_generation_id=scalp_generation,
        freshness=(
            "--" if item.market_data_age_seconds is None
            else f"{'STALE' if item.market_data_stale else 'LIVE'} · "
            f"{item.market_data_age_seconds:.1f}s"
        ),
    )


def _readable_blocker(reason: str) -> str:
    normalized = reason.strip().upper().replace(" ", "_")
    return {
        "NO_SETUP": "No Warrior setup detected",
        "SETUP": "No Warrior setup detected",
        "PRICE_TOO_LOW": "Price is below the Warrior range",
        "PRICE_TOO_HIGH": "Price is above the Warrior range",
        "CHANGE_TOO_LOW": "Percentage change requirement not met",
        "RVOL_LOW": "Scanner RVOL below formal threshold (informational)",
        "FLOAT_HIGH": "Float exceeds the Warrior limit",
        "SESSION_NOT_ALLOWED": "Current session is not allowed for Warrior execution",
        "SESSION": "Current session is not allowed for Warrior execution",
        "SPREAD_WIDE": "Historical spread decision; awaiting current execution quality",
        "SPREAD": "Waiting for spread/execution quality to improve",
        "EXECUTION_QUALITY_WAIT": "Waiting for spread/execution quality to improve",
        "INSUFFICIENT_NET_EXECUTABLE_EDGE": "Quick Scalper executable edge does not yet cover cost and risk",
        "POSITIVE_EXECUTABLE_EDGE": "Quick Scalper executable economics are positive",
        "NO_CATALYST": "Required catalyst is missing",
        "CATALYST_UNKNOWN": "Catalyst status is unavailable",
        "CATALYST": "Required catalyst is missing",
        "LIQUIDITY_LOW": "Current participation/liquidity requirement not met",
        "LIQUIDITY": "Liquidity requirement not met",
        "HALTED": "Symbol is halted",
        "HALT_UNKNOWN": "Halt status is unavailable",
        "HALT": "Symbol is halted",
        "NOT_TRADABLE": "Symbol is not tradable",
        "TRADABILITY": "Symbol is not tradable",
        "STOP_TOO_WIDE": "Stop distance exceeds risk limit",
        "STOP_INVALID": "Stop price is invalid",
        "BREAKOUT_NOT_CONFIRMED": "Breakout is not confirmed",
        "EXECUTION_NOT_ALLOWED": "Execution is not authorized",
        "RISK_REJECTED": "Warrior strategy/risk precondition not met",
        "STALE_MARKET_DATA": "Entry-critical market data is stale",
        "AWAITING_EXECUTION_QUOTE": "Waiting for fresh bid/ask",
        "STALE MARKET DATA": "Entry-critical market data is stale",
        "RISK": "Warrior strategy/risk precondition not met",
        "SCORE/RISK": "Warrior strategy/risk precondition not met",
        "SCANNER_RVOL": "Scanner RVOL below formal threshold (informational)",
        "PARTICIPATION": "Current participation/liquidity requirement not met",
        "STRATEGY_ELIGIBILITY": "Warrior strategy/risk precondition not met",
        "SETUP_SUPERSEDED": "Setup superseded by a newer generation",
        "SETUP_INVALIDATED": "Technical structure invalidated",
        "OPPORTUNITY_EXPIRED": "Opportunity expired",
        "PROVIDER_DATA_STALE": "Authoritative execution data is stale",
        "INSUFFICIENT_CURRENT_REWARD": "Insufficient reward remains at the executable price",
        "RISK_NOT_AUTHORIZED": "Risk authorization rejected",
        "ACCOUNT_NOT_AUTHORIZED": "Account authorization rejected",
        "AUTHORIZATION_REJECTED": "Authoritative entry authorization rejected",
        "OPPORTUNITY_GENERATION_CHANGED": "Displayed setup was superseded; awaiting the current generation",
    }.get(normalized, reason.replace("_", " ").capitalize())


def _catalyst_label(candidate) -> str:
    """Distinguish confirmed NONE from missing provider evidence."""
    status = candidate.catalyst_status.value
    if status == "UNAVAILABLE":
        return "UNAVAILABLE"
    if status == "UNKNOWN":
        return "UNKNOWN"
    return candidate.catalyst_type.value


__all__ = ["WarriorPaperView", "format_warrior_paper"]
