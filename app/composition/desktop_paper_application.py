"""Desktop composition helper for a real paper-runtime driver."""

from __future__ import annotations

from collections.abc import Callable

from .desktop import DesktopComposition, create_desktop_composition
from .desktop_runtime_config import DesktopRuntimeConfiguration
from .runtime_mode import RuntimeMode


def create_desktop_paper_composition(
    *,
    driver_factory: Callable[[], object],
) -> DesktopComposition:
    """Compose the desktop application around an explicit paper driver factory."""

    if not callable(driver_factory):
        raise TypeError("driver_factory must be callable")

    return create_desktop_composition(
        driver_factory=driver_factory,
        configuration=DesktopRuntimeConfiguration(
            runtime_mode=RuntimeMode.PAPER,
        ),
    )


__all__ = ["create_desktop_paper_composition"]
