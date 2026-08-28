"""Behavior-neutral explanations for the production setup detectors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .configuration import SetupConfig
from .features import contiguous_tail
from .models import MinuteBar, SetupState, SetupType
from .setups import (
    detect_bull_flag,
    detect_flat_top,
    detect_hod_breakout,
    detect_micro_pullback,
)


class SetupDiagnosticState(StrEnum):
    INSUFFICIENT_CONTIGUOUS_BARS = "INSUFFICIENT_CONTIGUOUS_BARS"
    SETUP_GEOMETRY_INVALID = "SETUP_GEOMETRY_INVALID"
    SETUP_FORMING = "SETUP_FORMING"
    SETUP_TRIGGER_NOT_CROSSED = "SETUP_TRIGGER_NOT_CROSSED"
    SETUP_TRIGGERED = "SETUP_TRIGGERED"


@dataclass(frozen=True, slots=True)
class SetupDiagnostic:
    setup_type: SetupType
    diagnostic_state: SetupDiagnosticState
    contiguous_bars: int
    required_bars: int
    trigger_status: SetupDiagnosticState | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "setup_type": self.setup_type.value,
            "diagnostic_state": self.diagnostic_state.value,
            "contiguous_bars": self.contiguous_bars,
            "required_bars": self.required_bars,
            "trigger_status": None if self.trigger_status is None else self.trigger_status.value,
        }


def production_setup_diagnostics(
    bars: tuple[MinuteBar, ...],
    config: SetupConfig = SetupConfig(),
) -> tuple[SetupDiagnostic, ...]:
    """Explain detector state without participating in setup selection."""

    available = len(contiguous_tail(bars))
    specs = (
        (SetupType.HIGH_OF_DAY_BREAKOUT, 5, detect_hod_breakout),
        (SetupType.MICRO_PULLBACK, 3 + config.minimum_pullback_bars + 1, detect_micro_pullback),
        (SetupType.BULL_FLAG, 4 + config.minimum_consolidation_bars + 1, detect_bull_flag),
        (SetupType.FLAT_TOP_BREAKOUT, config.flat_top_tests + 3, detect_flat_top),
    )
    output = []
    for setup_type, required, detector in specs:
        detection = detector(bars, config)
        if detection.state is SetupState.UNKNOWN:
            diagnostic = SetupDiagnosticState.INSUFFICIENT_CONTIGUOUS_BARS
        elif detection.state is SetupState.NOT_FORMED:
            diagnostic = SetupDiagnosticState.SETUP_GEOMETRY_INVALID
        elif detection.state is SetupState.FORMING:
            diagnostic = SetupDiagnosticState.SETUP_FORMING
            trigger_status = SetupDiagnosticState.SETUP_TRIGGER_NOT_CROSSED
        else:
            diagnostic = SetupDiagnosticState.SETUP_TRIGGERED
            trigger_status = SetupDiagnosticState.SETUP_TRIGGERED
        if detection.state in {SetupState.UNKNOWN, SetupState.NOT_FORMED}:
            trigger_status = None
        output.append(SetupDiagnostic(setup_type, diagnostic, available, required, trigger_status))
    return tuple(output)


__all__ = ["SetupDiagnostic", "SetupDiagnosticState", "production_setup_diagnostics"]
