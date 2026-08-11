"""Coalesced Warrior paper read-model formatting for Mission Control."""

from __future__ import annotations

from dataclasses import dataclass

from app.gui.models.watchlist import WatchlistRow, WatchlistSnapshot
from app.strategies.warrior_momentum.desktop_sidecar import WarriorPaperSnapshot


@dataclass(frozen=True, slots=True)
class WarriorPaperView:
    focus: WatchlistSnapshot
    summary: str
    funnel: str
    research: str


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
    )


def _row(item) -> WatchlistRow:
    candidate = item.candidate
    setup = candidate.setup
    blocking = ", ".join(item.blocking_reasons) or "--"
    explanations = (*candidate.explanations, *(
        (f"Blocked: {blocking}",) if blocking != "--" else ()
    ))
    return WatchlistRow(
        symbol=candidate.symbol, selected=False,
        latest_price=f"{candidate.price:,.2f}", change="--",
        change_percent=f"{candidate.percentage_change:+.2f}%",
        bid="--", ask="--", volume=f"{candidate.volume:,.0f}",
        market_status=candidate.session, last_update=candidate.timestamp.isoformat(),
        stale="LIVE", rank=str(candidate.rank), score=f"{candidate.score.total:.2f}",
        relative_volume=f"{candidate.relative_volume:.2f}x",
        dollar_volume=f"${candidate.dollar_volume:,.0f}",
        spread="--" if candidate.spread_percent is None else f"{candidate.spread_percent:.2f}%",
        catalyst=candidate.catalyst_status.value, session=candidate.session,
        float_shares=("--" if candidate.float_shares is None else f"{candidate.float_shares / 1_000_000:.1f}M"),
        setup="--" if setup is None else setup.setup_type.value.replace("_", " "),
        setup_state="UNKNOWN" if setup is None else setup.state.value,
        distance_to_hod=("--" if candidate.distance_from_hod_percent is None else f"{candidate.distance_from_hod_percent:.2f}%"),
        strategy_status=("ENTRY BLOCKED" if blocking != "--" and setup is not None and setup.state.value == "TRIGGERED" else candidate.status.value.replace("_", " ")),
        explanations=" | ".join(explanations),
        float_provenance=item.float_provenance.value.replace("MARKET_CAP_PRICE_PROXY", "MCAP/PRICE PROXY"),
        entry_trigger="--" if item.entry_trigger is None else f"{item.entry_trigger:,.4f}",
        stop_price="--" if item.stop_price is None else f"{item.stop_price:,.4f}",
        blocking_reasons=blocking,
    )


__all__ = ["WarriorPaperView", "format_warrior_paper"]
