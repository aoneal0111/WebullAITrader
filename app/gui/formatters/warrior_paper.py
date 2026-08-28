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
    )


def _row(item) -> WatchlistRow:
    candidate = item.candidate
    setup = candidate.setup
    # Candidate reason codes include non-blocking quality facts (for example
    # catalyst NONE under Balanced V1).  The sidecar's entry blockers are the
    # authoritative presentation source.
    raw_blockers = tuple(dict.fromkeys(item.blocking_reasons))
    readable_blockers = tuple(dict.fromkeys(
        _readable_blocker(reason) for reason in raw_blockers
    ))
    blocking = "\n".join(readable_blockers) or "--"
    explanations = (*candidate.explanations, *(
        (f"Blocked: {blocking}",) if blocking != "--" else ()
    ))
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
        catalyst=candidate.catalyst_type.value, session=candidate.session,
        float_shares=("--" if candidate.float_shares is None else f"{candidate.float_shares / 1_000_000:.1f}M"),
        setup="NO SETUP" if setup is None else setup.setup_type.value.replace("_", " "),
        setup_state="--" if setup is None else setup.state.value,
        distance_to_hod=("--" if candidate.distance_from_hod_percent is None else f"{candidate.distance_from_hod_percent:.2f}%"),
        strategy_status=("ENTRY BLOCKED" if blocking != "--" and setup is not None and setup.state.value == "TRIGGERED" else candidate.status.value.replace("_", " ")),
        explanations=" | ".join(explanations),
        float_provenance=item.float_provenance.value.replace("MARKET_CAP_PRICE_PROXY", "MCAP/PRICE PROXY"),
        entry_trigger=format_price(item.entry_trigger),
        stop_price=format_price(item.stop_price),
        blocking_reasons=blocking,
        warrior_evaluated=True,
        warrior_score=f"{candidate.score.total:.2f}",
        warrior_status=candidate.status.value.replace("_", " "),
        warrior_session=candidate.session,
        strategy_name="Warrior Momentum",
        market_timestamp=candidate.timestamp.isoformat(),
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
        "RVOL_LOW": "Relative volume requirement not met",
        "FLOAT_HIGH": "Float exceeds the Warrior limit",
        "SESSION_NOT_ALLOWED": "Current session is not allowed for Warrior execution",
        "SESSION": "Current session is not allowed for Warrior execution",
        "SPREAD_WIDE": "Spread is too wide",
        "SPREAD": "Spread is too wide",
        "NO_CATALYST": "Required catalyst is missing",
        "CATALYST_UNKNOWN": "Catalyst status is unavailable",
        "CATALYST": "Required catalyst is missing",
        "LIQUIDITY_LOW": "Liquidity requirement not met",
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
        "RISK_REJECTED": "Risk engine rejected the entry",
        "STALE_MARKET_DATA": "Entry-critical market data is stale",
        "AWAITING_EXECUTION_QUOTE": "Waiting for fresh bid/ask",
        "STALE MARKET DATA": "Entry-critical market data is stale",
        "RISK": "Risk gate rejected the entry",
        "SCORE/RISK": "Risk engine rejected the entry",
    }.get(normalized, reason.replace("_", " ").capitalize())


__all__ = ["WarriorPaperView", "format_warrior_paper"]
