"""Configuration values for desktop runtime selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .runtime_mode import RuntimeMode


@dataclass(frozen=True, slots=True)
class DesktopRuntimeConfiguration:
    """Select the desktop runtime without constructing its dependencies."""

    runtime_mode: RuntimeMode = RuntimeMode.SIMULATED
    recording_directory: Path = Path(".atlas_recordings")

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_mode, RuntimeMode):
            raise TypeError("runtime_mode must be a RuntimeMode")
        if not isinstance(self.recording_directory, Path):
            raise TypeError("recording_directory must be a Path")


__all__ = ["DesktopRuntimeConfiguration"]
