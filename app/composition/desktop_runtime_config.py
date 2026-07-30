"""Configuration values for desktop runtime selection."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime_mode import RuntimeMode


@dataclass(frozen=True, slots=True)
class DesktopRuntimeConfiguration:
    """Select the desktop runtime without constructing its dependencies."""

    runtime_mode: RuntimeMode = RuntimeMode.PAPER


__all__ = ["DesktopRuntimeConfiguration"]
