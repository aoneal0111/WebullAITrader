"""Desktop runtime mode selection."""

from __future__ import annotations

from enum import Enum


class RuntimeMode(str, Enum):
    """Supported desktop execution environments."""

    SIMULATED = "SIMULATED"
    PAPER = "PAPER"


__all__ = ["RuntimeMode"]
