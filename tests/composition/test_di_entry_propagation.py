from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from time import monotonic, sleep
from types import SimpleNamespace

from app.configuration import load_configuration
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.scanner_adapter import MarketEventScannerAdapter, ScannerReferenceData, ScannerReferenceStore
from app.momentum_scanner.models import CatalystStatus, CatalystType
from tests.trade_intelligence.test_entry_timing import _result
from tests.warrior_momentum.test_forward_capture import bars
import app.composition.desktop as desktop_module


def test_composed_market_event_ingress_reaches_experiment_journal(monkeypatch, tmp_path):
    # A fixed weekday keeps scanner trading-day resolution deterministic.
    # Using wall-clock "now" makes this composition test fail on weekends.
    now = datetime(2026, 8, 10, 14, 50, tzinfo=UTC)
    configuration = load_configuration({
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED": "true",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE": "PAPER_TREATMENT",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH": str(tmp_path / "experiment.sqlite3"),
        "WARRIOR_FORWARD_PAPER_ENABLED": "true",
        "WARRIOR_FORWARD_CAPTURE_PATH": str(tmp_path / "forward_capture.sqlite3"),
        "ALLOWED_SYMBOLS": "",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    captured = {}

    class Runtime:
        def close(self, *, timeout_seconds=5.0):
            return True

    def runtime_factory(*args, **kwargs):
        captured["observer"] = kwargs["market_event_observer"]
        return Runtime()

    monkeypatch.setattr(desktop_module, "create_desktop_runtime_service", runtime_factory)
    composition = desktop_module.create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
        paper_clock=lambda: now,
    )
    observer = captured["observer"]
    reference = ScannerReferenceData(
        "XYZ", D("8"), D("100000"), D("6000000"), CatalystType.EARNINGS,
        "Earnings", True, now, CatalystStatus.TRUE, D("1000000"),
    )
    scanner = MarketEventScannerAdapter(ScannerReferenceStore((reference,)))
    observer.bind_scanner_adapter(scanner)
    sidecar = composition.warrior_forward_sidecar
    assert sidecar is not None
    canonical_bars = bars()
    sidecar.preload_historical_bars("XYZ", tuple(
        SimpleNamespace(
            timestamp=now - timedelta(minutes=(len(canonical_bars) - index)),
            open=item.open, high=item.high, low=item.low, close=item.close,
            volume=item.volume,
        )
        for index, item in enumerate(canonical_bars)
    ))
    observer.start("PAPER")
    try:
        # Keep the real composition/market-event ingress, but make the DI-2
        # result deterministic so this test isolates propagation rather than
        # the Warrior geometry fixture.
        sidecar._decision_intelligence_observer.observe_decision = lambda **kwargs: _result()
        scanner.consume(MarketEvent(1, now, "XYZ", "test", MarketEventType.QUOTE,
                                    QuotePayload(D("10.18"), D("10.22"), D("100"), D("100"))))
        event = MarketEvent(2, now + timedelta(seconds=1), "XYZ", "test",
                            MarketEventType.TRADE, TradePayload(D("10.20"), D("100"), "trade-2"))
        scanner.consume(event)
        observer(event)
        handoff = observer._warrior_handoff
        assert handoff is not None
        deadline = monotonic() + 2.0
        while handoff.metrics().processed < handoff.metrics().submitted:
            assert monotonic() < deadline
            sleep(0.01)
        assert sidecar._service is not None
        assert sidecar._service.wait_for_intelligence(timeout_seconds=2.0)
        journal = composition.paper_entry_intelligence._journal
        assert journal is not None
        assignment_count = journal._connection.execute(
            "SELECT COUNT(*) FROM experiment_assignments"
        ).fetchone()[0]
        assert assignment_count > 0, sidecar._service.intelligence_worker_metrics()
        assert journal._connection.execute("SELECT COUNT(*) FROM experiment_decisions").fetchone()[0] > 0
        marker = journal._connection.execute(
            "SELECT historical_entry_experiment_mode, historical_entry_experiment_path "
            "FROM experiment_runtime_markers ORDER BY startup_timestamp DESC LIMIT 1"
        ).fetchone()
        assert tuple(marker) == ("PAPER_TREATMENT", str(configuration.historical_entry_experiment_path))
    finally:
        composition.close(timeout_seconds=1.0)
