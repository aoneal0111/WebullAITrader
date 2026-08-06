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
        ("Objective", None),
        ("Runtime Mode", runtime.environment),
        ("Market Session", health.market_session_status),
        ("AI Scanner", health.scanner_status),
        ("Decision Engine", health.ai_status or runtime.inference_status),
        ("Risk Engine", health.risk_status),
        ("Runtime Health", health.runtime_status or runtime.phase.value),
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
    if latest is not None:
        return AIThinkingSnapshot(
            state="Decision recorded",
            detail=(
                f"{latest.action} {latest.symbol} · "
                f"{latest.confidence}% confidence"
            ),
            reasoning=latest.reasoning_summary,
            last_decision=f"{latest.action} {latest.symbol}",
            tone="good",
        )

    runtime = state.runtime
    scanner = (state.health_projection.scanner_status or "").upper()
    positions = state.portfolio_projection.open_positions
    if positions > 0:
        thinking_state = "Managing active positions"
        detail = f"{positions} active position{'s' if positions != 1 else ''}."
        tone = "good"
    elif runtime.phase is RuntimePhase.RUNNING and not scanner.startswith("PAUSED"):
        thinking_state = "Searching"
        detail = "Waiting for a runtime decision."
        tone = "good"
    else:
        thinking_state = "Waiting for next scan"
        detail = "No runtime decision reasoning is available."
        tone = "warn" if scanner.startswith("PAUSED") else "neutral"
    return AIThinkingSnapshot(
        state=thinking_state,
        detail=detail,
        reasoning="Unknown",
        last_decision="Unknown",
        tone=tone,
    )


def _display(value: object | None) -> str:
    if value is None or value == "" or value == "--":
        return "Unknown"
    if isinstance(value, str):
        return value.replace("_", " ").title()
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
