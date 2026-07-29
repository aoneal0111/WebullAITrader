"""Composition-owned fan-out for paper-runtime events."""

from __future__ import annotations

from collections.abc import Iterable

from app.operations.runtime import PaperRuntimeEvent, RuntimeEventSink


class CompositeRuntimeEventSink:
    """Forward each runtime event to an ordered set of injected consumers.

    The runtime engine continues to depend on one ``RuntimeEventSink`` while the
    composition layer owns the decision to publish an event to multiple
    projections or adapters.
    """

    def __init__(
        self,
        sinks: Iterable[RuntimeEventSink | None],
    ) -> None:
        normalized: list[RuntimeEventSink] = []

        for sink in sinks:
            if sink is None:
                continue
            if not callable(sink):
                raise TypeError(
                    "every runtime event sink must be callable"
                )
            normalized.append(sink)

        self._sinks = tuple(normalized)

    @property
    def sinks(self) -> tuple[RuntimeEventSink, ...]:
        """Return the immutable sink registration order."""

        return self._sinks

    def __call__(self, event: PaperRuntimeEvent) -> None:
        """Publish one event to every registered sink in order."""

        for sink in self._sinks:
            sink(event)


__all__ = ["CompositeRuntimeEventSink"]
