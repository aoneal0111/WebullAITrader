from __future__ import annotations

from collections.abc import Callable, Iterable
from time import perf_counter

from app.market_data.models import MarketEvent
from app.performance_diagnostics import performance_diagnostics
from app.scanner_adapter.adapter import MarketEventScannerAdapter

from .projection_handoff import BoundedProjectionHandoff


class CompositeMarketEventObserver:
    """Keep authoritative Warrior work ordered; offload advisory projections."""

    def __init__(
        self,
        primary: Callable[[MarketEvent], object] | None,
        warrior: object,
        research: object | None = None,
        adaptive_entry: object | None = None,
        *,
        async_projections: bool = False,
        projection_capacity: int = 512,
    ) -> None:
        self.primary = primary
        self.warrior = warrior
        self.research = research
        self.adaptive_entry = adaptive_entry
        self.adaptive_entry_failures = 0
        self.async_projections = bool(async_projections)
        self._research_handoff = None
        self._adaptive_handoff = None
        if self.async_projections:
            self._research_handoff = BoundedProjectionHandoff(
                self._dispatch_research,
                maximum_keys=projection_capacity,
            )
            self._adaptive_handoff = BoundedProjectionHandoff(
                self._dispatch_adaptive,
                maximum_keys=projection_capacity,
            )
            setter = getattr(self.warrior, "set_research_observer", None)
            if research is not None and callable(setter):
                setter(_ResearchHandoffProxy(self._research_handoff))

    def start(self, environment: str | None = None) -> None:
        if self._research_handoff is not None:
            self._research_handoff.start()
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.start()
        research_start = getattr(self.research, "start", None)
        if callable(research_start):
            research_start(environment)
        adaptive_start = getattr(self.adaptive_entry, "start", None)
        if callable(adaptive_start):
            adaptive_start(environment)
        self.warrior.start(environment)

    def stop(self) -> None:
        if self._research_handoff is not None:
            self._research_handoff.stop(drain=False)
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.stop(drain=False)
        research_stop = getattr(self.research, "stop", None)
        if callable(research_stop):
            try:
                research_stop()
            except Exception:
                pass
        adaptive_stop = getattr(self.adaptive_entry, "stop", None)
        if callable(adaptive_stop):
            try:
                adaptive_stop()
            except Exception:
                pass
        self.warrior.stop()

    def projection_metrics(self) -> dict[str, dict[str, int | float]]:
        return {
            "research": (
                {}
                if self._research_handoff is None
                else self._research_handoff.memory_metrics()
            ),
            "adaptive": (
                {}
                if self._adaptive_handoff is None
                else self._adaptive_handoff.memory_metrics()
            ),
        }

    def _dispatch_research(self, value: object) -> None:
        if self.research is None:
            return
        kind, payload = value
        if kind == "event":
            self.research(payload)
        elif kind == "decision":
            callback = getattr(self.research, "observe_scanner_decision", None)
            if callable(callback):
                callback(payload)
        else:
            point, candidate, signal = payload
            callback = getattr(self.research, "observe_warrior_decision", None)
            if callable(callback):
                callback(point, candidate, signal)

    def _dispatch_adaptive(self, event: object) -> None:
        if not callable(self.adaptive_entry):
            return
        try:
            self.adaptive_entry(event)
        except Exception:
            self.adaptive_entry_failures += 1

    def __call__(self, event: MarketEvent) -> None:
        if self.primary is not None:
            self._timed("paper.market_event", self.primary, event)
        self._timed("warrior.desktop_sidecar", self.warrior, event)
        if self._research_handoff is not None:
            self._research_handoff.submit(
                _projection_key(event),
                ("event", event),
            )
        elif callable(self.research):
            self._timed("research.trade_intelligence", self.research, event)
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.submit(_projection_key(event), event)
        elif callable(self.adaptive_entry):
            try:
                self._timed("research.adaptive_entry", self.adaptive_entry, event)
            except Exception:
                self.adaptive_entry_failures += 1

    @staticmethod
    def _timed(
        component: str,
        observer: Callable[[MarketEvent], object],
        event: MarketEvent,
    ) -> object:
        started = perf_counter()
        success = False
        try:
            result = observer(event)
            success = True
            return result
        finally:
            performance_diagnostics.record_component_duration(
                component,
                (perf_counter() - started) * 1000.0,
                event_type=getattr(
                    getattr(event, "event_type", None),
                    "value",
                    None,
                ),
                symbol=getattr(event, "symbol", None),
                success=success,
            )

    def observe_scanner_decision(self, decision: object) -> None:
        observer = getattr(self.research, "observe_scanner_decision", None)
        if callable(observer):
            if self._research_handoff is not None:
                self._research_handoff.submit(
                    _projection_key(decision),
                    ("decision", decision),
                )
            else:
                observer(decision)

    def reset_symbol(self, symbol: str) -> None:
        observer = getattr(self.research, "reset_symbol", None)
        if callable(observer):
            observer(symbol)

    def bind_scanner_adapter(self, adapter: MarketEventScannerAdapter) -> None:
        self.warrior.bind_scanner_adapter(adapter)

    def bind_scanner_decision_source(
        self,
        source: Callable[[str], object | None],
        ranked_source: Callable[[str], bool] | None = None,
    ) -> None:
        self.warrior.bind_scanner_decision_source(source, ranked_source)

    def needs_historical_preload(self, symbol: str) -> bool:
        return self.warrior.needs_historical_preload(symbol)

    def preload_historical_bars(
        self,
        symbol: str,
        bars: Iterable[object],
    ) -> int:
        return self.warrior.preload_historical_bars(symbol, bars)

    def retained_symbols(self) -> tuple[str, ...]:
        values = set(self.warrior.retained_symbols())
        research_values = getattr(self.research, "retained_symbols", None)
        if callable(research_values):
            values.update(research_values())
        return tuple(sorted(values))


class _ResearchHandoffProxy:
    """Advisory TI facade used by the authoritative Warrior sidecar."""

    def __init__(self, handoff: BoundedProjectionHandoff) -> None:
        self._handoff = handoff

    def __call__(self, event: object) -> None:
        self._handoff.submit(_projection_key(event), ("event", event))

    def observe_warrior_decision(
        self,
        point: object,
        candidate: object,
        signal: object,
    ) -> None:
        self._handoff.submit(
            _projection_key(candidate),
            ("warrior", (point, candidate, signal)),
        )


def _projection_key(value: object) -> str:
    symbol = getattr(value, "symbol", None)
    if symbol is None and isinstance(value, tuple) and value:
        symbol = getattr(value[0], "symbol", None)
    return str(symbol or "__GLOBAL__").strip().upper()


__all__ = ["CompositeMarketEventObserver"]
