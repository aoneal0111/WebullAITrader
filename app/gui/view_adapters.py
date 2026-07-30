from __future__ import annotations

from collections.abc import Callable


class RenderAdapter:
    """Adapt a focused render method to the presenter's render protocol."""

    def __init__(self, renderer: Callable[[object], None]) -> None:
        if not callable(renderer):
            raise TypeError("renderer must be callable")
        self._renderer = renderer

    def render(self, snapshot) -> None:
        self._renderer(snapshot)


__all__ = ["RenderAdapter"]
