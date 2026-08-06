from __future__ import annotations

from app.gui.models import (
    AIThinkingSnapshot,
    MissionStatusRow,
    MissionStatusSnapshot,
)
from app.operations_core import ApplicationState, RuntimePhase


def project_mission_status(state: ApplicationState) -> MissionStatusSnapshot:
    health = state.health_projection
    runtime = state.runtime
    values = (
        ("Objective", _objective(state)),
        ("Runtime", runtime.phase.value),
        ("Market Session", health.market_session_status),
        ("AI Scanner", health.scanner_status),
        ("Decision Engine", health.ai_status or runtime.inference_status),
        ("Risk Engine", health.risk_status),
        (
            "System Health",
            "HEALTHY (RUNNING)" if health.healthy else "DEGRADED" if health.degraded
            else health.runtime_status or runtime.phase.value,
        ),
    )
    return MissionStatusSnapshot(rows=tuple(
        MissionStatusRow(label, _display(value), _tone(value))
        for label, value in values
    ))


def project_ai_thinking(state: ApplicationState) -> AIThinkingSnapshot:
    latest = max(
        state.decision_projection.decisions,
        key=lambda decision: decision.timestamp,
        default=None,
    )
    runtime = state.runtime
    scanner = (state.health_projection.scanner_status or "").upper()
    positions = state.portfolio_projection.open_positions
    if positions > 0:
        operational_state = "Managing active positions."
        tone = "good"
    elif runtime.phase is RuntimePhase.RUNNING and not scanner.startswith("PAUSED"):
        has_ranked_candidates = any(
            dict(entry.metadata).get("scanner_rank") is not None
            for entry in state.watchlist_projection.entries
        )
        operational_state = (
            "Evaluating high-confidence candidates."
            if has_ranked_candidates
            else "Searching for opportunities."
        )
        tone = "good"
    elif scanner.startswith("PAUSED"):
        operational_state = "AI Scanner paused."
        tone = "warn"
    else:
        operational_state = "Waiting for the next scan cycle."
        tone = "neutral"
    return AIThinkingSnapshot(
        objective=_objective(state),
        operational_state=operational_state,
        reasoning=(
            latest.reasoning_summary
            if latest is not None and latest.reasoning_summary
            else "Unknown"
        ),
        last_decision=(
            f"{latest.action} {latest.symbol}"
            if latest is not None
            else "Unknown"
        ),
        next_evaluation="Unknown",
        confidence=(
            f"{latest.confidence}%" if latest is not None else "Unknown"
        ),
        tone=tone,
    )


def _objective(state: ApplicationState) -> str:
    if state.portfolio_projection.open_positions > 0:
        return "Managing Active Positions"
    scanner = (state.health_projection.scanner_status or "").upper()
    if (
        state.runtime.phase is RuntimePhase.RUNNING
        and not scanner.startswith("PAUSED")
    ):
        return "Searching for Opportunities"
    return "Unknown"


def _display(value: object | None) -> str:
    if value is None or value == "" or value == "--":
        return "Unknown"
    if isinstance(value, str):
        normalized = value.replace("_", " ")
        return normalized.title() if value == value.upper() else normalized
    return str(value)


def _tone(value: object | None) -> str:
    normalized = str(value or "").upper()
    if any(
        word in normalized
        for word in ("FAILED", "ERROR", "DISCONNECTED", "UNAVAILABLE")
    ):
        return "danger"
    if any(
        word in normalized
        for word in ("PAUSED", "STARTING", "DEGRADED", "STOPPING")
    ):
        return "warn"
    if any(
        word in normalized
        for word in ("RUNNING", "CONNECTED", "HEALTHY", "READY", "ACTIVE")
    ):
        return "good"
    return "neutral"


__all__ = ["project_ai_thinking", "project_mission_status"]
