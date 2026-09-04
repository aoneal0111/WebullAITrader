"""Bounded read-only view of production universe-admission observations."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock


class ProductionUniverseComparisonTracker:
    """Receives production facts without returning a production decision."""

    def __init__(self, *, maximum_symbols: int = 1000) -> None:
        if maximum_symbols <= 0:
            raise ValueError("comparison symbol bound must be positive")
        self._maximum_symbols = maximum_symbols
        self._lock = RLock()
        self._stages: OrderedDict[str, set[str]] = OrderedDict()

    def begin_refresh(self, **_: object) -> None:
        with self._lock:
            self._stages.clear()

    def record(self, **values: object) -> None:
        try:
            symbol = str(
                values.get("normalized_symbol")
                or values.get("raw_symbol")
                or ""
            ).strip().upper()
            if not symbol:
                return
            stage_value = values.get("stage")
            stage = str(getattr(stage_value, "value", stage_value)).strip().upper()
            source = str(values.get("screener_identity") or "").strip().upper()
            with self._lock:
                facts = self._stages.setdefault(symbol, set())
                if stage:
                    facts.add(stage)
                if source:
                    facts.add(source)
                self._stages.move_to_end(symbol)
                while len(self._stages) > self._maximum_symbols:
                    self._stages.popitem(last=False)
        except Exception:
            return

    def stages_for(self, symbol: str) -> tuple[str, ...]:
        normalized = symbol.strip().upper()
        with self._lock:
            return tuple(sorted(self._stages.get(normalized, ())))

    def retained_symbols(self) -> int:
        with self._lock:
            return len(self._stages)

    def estimated_retained_bytes(self) -> int:
        with self._lock:
            return sum(256 + 64 * len(values) for values in self._stages.values())

    def close(self, **_: object) -> bool:
        return True


class UniverseAdmissionObserverFanout:
    """One-way fanout whose observer return values are deliberately discarded."""

    def __init__(self, *observers: object) -> None:
        self._observers = tuple(value for value in observers if value is not None)

    def begin_refresh(self, **values: object) -> None:
        for observer in self._observers:
            callback = getattr(observer, "begin_refresh", None)
            if callable(callback):
                try:
                    callback(**values)
                except Exception:
                    pass

    def record(self, **values: object) -> None:
        for observer in self._observers:
            callback = getattr(observer, "record", None)
            if callable(callback):
                try:
                    callback(**values)
                except Exception:
                    pass

    def close(self, **values: object) -> bool:
        stopped = True
        for observer in self._observers:
            callback = getattr(observer, "close", None)
            if callable(callback):
                try:
                    stopped = bool(callback(**values)) and stopped
                except Exception:
                    stopped = False
        return stopped


__all__ = ["ProductionUniverseComparisonTracker", "UniverseAdmissionObserverFanout"]
