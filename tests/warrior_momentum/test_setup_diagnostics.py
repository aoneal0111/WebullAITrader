from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.models import MinuteBar, SetupType
from app.strategies.warrior_momentum.setup_diagnostics import (
    SetupDiagnosticState,
    production_setup_diagnostics,
)


def _bars(count: int) -> tuple[MinuteBar, ...]:
    start = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
    return tuple(
        MinuteBar(
            "TEST", start + timedelta(minutes=index),
            Decimal("2"), Decimal("2.01"), Decimal("1.99"), Decimal("2"), Decimal("1000"),
        )
        for index in range(count)
    )


def test_diagnostics_distinguish_contiguous_latency_and_geometry() -> None:
    short = {item.setup_type: item for item in production_setup_diagnostics(_bars(3))}
    assert short[SetupType.MICRO_PULLBACK].diagnostic_state is SetupDiagnosticState.INSUFFICIENT_CONTIGUOUS_BARS
    assert short[SetupType.MICRO_PULLBACK].required_bars == 6

    enough = {item.setup_type: item for item in production_setup_diagnostics(_bars(6))}
    assert enough[SetupType.MICRO_PULLBACK].diagnostic_state is SetupDiagnosticState.SETUP_GEOMETRY_INVALID
    assert enough[SetupType.MICRO_PULLBACK].contiguous_bars == 6
    assert enough[SetupType.MICRO_PULLBACK].trigger_status is None


def test_diagnostics_are_observability_only() -> None:
    first = production_setup_diagnostics(_bars(6))
    second = production_setup_diagnostics(_bars(6))
    assert first == second
    assert all("execution" not in item.as_payload() for item in first)
