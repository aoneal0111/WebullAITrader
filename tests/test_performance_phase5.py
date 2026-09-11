"""Deterministic progressive reference-readiness tests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread

from app.momentum_scanner import AssetClass
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.universe import SecurityType, UniverseSelection, UniverseSymbol


@dataclass(frozen=True)
class _Reference:
    as_of: object = None


class _Pipeline:
    def __init__(self) -> None:
        self.events = []

    def consume(self, event):
        self.events.append(event)
        return None


def _symbol(name: str) -> UniverseSymbol:
    return UniverseSymbol(
        name,
        AssetClass.STOCK,
        "NASDAQ",
        SecurityType.COMMON_STOCK,
        True,
    )


class _Universe:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.selection = UniverseSelection(tuple(_symbol(name) for name in names), ())

    def select_all(self, asset_classes):
        return self.selection


class _References:
    def __init__(self, *, blocked: str | None = None, failures=()) -> None:
        self.blocked = blocked
        self.failures = set(failures)
        self.calls: list[str] = []
        self.release = Event()

    def get_for_instrument(self, item, *, force_refresh=False):
        self.calls.append(item.symbol)
        if item.symbol == self.blocked:
            self.release.wait(2.0)
        if item.symbol in self.failures:
            raise RuntimeError("reference unavailable")
        return _Reference()


def test_first_reference_ready_publishes_before_full_warmup() -> None:
    references = _References(blocked="B")
    engine = RealtimeScannerEngine(
        _Universe(("A", "B", "C")), references, _Pipeline()
    )
    ready = Event()
    promotions: list[tuple[str, ...]] = []
    engine.set_reference_ready_observer(
        lambda: (promotions.append(engine.active_symbols), ready.set())
    )
    engine.prepare_universe((AssetClass.STOCK,))

    worker = Thread(
        target=engine.refresh_universe,
        args=((AssetClass.STOCK,),),
    )
    worker.start()
    assert ready.wait(1.0)
    assert promotions == [("A",)]
    assert engine.pending_reference_symbols == ("B", "C")
    assert engine.failed_reference_symbols == ()
    references.release.set()
    worker.join(1.0)
    assert not worker.is_alive()


def test_pending_reference_cannot_become_qualified() -> None:
    references = _References(blocked="A")
    engine = RealtimeScannerEngine(
        _Universe(("A",)), references, _Pipeline()
    )
    engine.prepare_universe((AssetClass.STOCK,))
    assert engine.active_symbols == ()
    assert engine.pending_reference_symbols == ("A",)
    assert engine.snapshot().healthy is False
    references.release.set()


def test_failed_reference_is_terminally_rejected_not_a_pass() -> None:
    references = _References(failures=("BAD",))
    engine = RealtimeScannerEngine(
        _Universe(("GOOD", "BAD")), references, _Pipeline()
    )
    engine.refresh_universe((AssetClass.STOCK,))
    assert engine.active_symbols == ("GOOD",)
    assert engine.failed_reference_symbols == ("BAD",)
    assert engine.snapshot().reference_failures[0].symbol == "BAD"


def test_all_reference_work_reaches_terminal_state_before_completion() -> None:
    references = _References(failures=("BAD",))
    engine = RealtimeScannerEngine(
        _Universe(("GOOD", "BAD")), references, _Pipeline()
    )
    engine.refresh_universe((AssetClass.STOCK,))
    assert engine.pending_reference_symbols == ()
    assert engine.active_symbols == ("GOOD",)
    assert engine.failed_reference_symbols == ("BAD",)
    assert set(references.calls) == {"GOOD", "BAD"}


def test_duplicate_symbol_is_requested_once_per_refresh() -> None:
    references = _References()
    engine = RealtimeScannerEngine(
        _Universe(("DBGI", "DBGI")), references, _Pipeline()
    )
    engine.prepare_universe((AssetClass.STOCK,))
    engine.refresh_universe((AssetClass.STOCK,))
    assert references.calls == ["DBGI"]


def test_reference_failure_does_not_block_other_symbol_readiness() -> None:
    references = _References(failures=("BAD",))
    engine = RealtimeScannerEngine(
        _Universe(("BAD", "GOOD")), references, _Pipeline()
    )
    promotions: list[tuple[str, ...]] = []
    engine.set_reference_ready_observer(lambda: promotions.append(engine.active_symbols))
    engine.refresh_universe((AssetClass.STOCK,))
    assert promotions == [("GOOD",)]

