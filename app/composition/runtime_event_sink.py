"""Composition-owned fan-out for paper-runtime events."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from app.operations.runtime import PaperRuntimeEvent, RuntimeEventSink


_LOGGER = logging.getLogger(__name__)


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
            try:
                sink(event)
            except Exception as exc:
                marker = getattr(sink, "mark_degraded", None)
                if callable(marker):
                    try:
                        marker()
                    except Exception:
                        _LOGGER.exception(
                            "runtime projection degradation marker failed"
                        )
                sink_name = getattr(sink, "__qualname__", None) or type(sink).__name__
                fill_id = None if event.fill is None else event.fill.request_id
                order_id = None if event.order is None else event.order.order_id
                _LOGGER.critical(
                    "runtime projection sink failed; continuing independent sinks "
                    "sink=%s event_type=%s symbol=%s order_id=%s fill_id=%s "
                    "projection_health=%s error=%s: %s",
                    sink_name, event.event_type, event.symbol or "--",
                    order_id or "--", fill_id or "--",
                    getattr(sink, "health", "DEGRADED"), type(exc).__name__, exc,
                    exc_info=True,
                )


__all__ = ["CompositeRuntimeEventSink"]
