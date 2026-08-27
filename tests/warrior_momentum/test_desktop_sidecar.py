from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gui.formatters.warrior_paper import format_warrior_paper
from app.live_scanner.session import scanner_session
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.scanner_adapter import MarketEventScannerAdapter, ScannerReferenceData, ScannerReferenceStore
from app.strategies.warrior_momentum import (
    CaptureRecord, CaptureRecordType, EvidenceMaturity, FloatProvenance,
    ForwardCaptureStore, MinuteBar, RiskConfig, WarriorCaptureHealth,
    WarriorDesktopSidecar, WarriorFocusItem, WarriorMomentumConfig,
    WarriorMomentumRuntime, WarriorPaperSnapshot, build_cumulative_reports,
    evidence_maturity, strategy_configuration_fingerprint,
)

T0 = datetime(2026, 8, 11, 14, 30, tzinfo=UTC)


def adapter() -> MarketEventScannerAdapter:
    reference = ScannerReferenceData(
        "XYZ", D("8"), D("100000"), D("6000000"),
        CatalystType.EARNINGS, "Earnings", True, T0,
        CatalystStatus.TRUE, D("1000000"),
    )
    return MarketEventScannerAdapter(ScannerReferenceStore((reference,)))


def quote(at=T0) -> MarketEvent:
    return MarketEvent(1, at, "XYZ", "test", MarketEventType.QUOTE,
                       QuotePayload(D("10.18"), D("10.22"), D("100"), D("100")))


def trade(sequence: int, at: datetime, price: str, size: str = "100") -> MarketEvent:
    return MarketEvent(sequence, at, "XYZ", "test", MarketEventType.TRADE,
                       TradePayload(D(price), D(size), f"trade-{sequence}"))


def deliver(scanner, sidecar, event) -> None:
    scanner.consume(event)
    sidecar(event)


def test_disabled_sidecar_is_inert_and_default_configuration_is_frozen(tmp_path: Path) -> None:
    sidecar = WarriorDesktopSidecar(enabled=False, storage_path=tmp_path / "disabled.sqlite3")
    sidecar.start("PAPER")
    sidecar(quote())
    assert sidecar.snapshot().health is WarriorCaptureHealth.DISABLED
    assert not (tmp_path / "disabled.sqlite3").exists()
    assert strategy_configuration_fingerprint() == strategy_configuration_fingerprint()
    changed = WarriorMomentumConfig(risk=RiskConfig(configured_per_trade_risk=D("101")))
    assert strategy_configuration_fingerprint(changed) != strategy_configuration_fingerprint()
    assert scanner_session(datetime(2026, 8, 11, 12, 0, tzinfo=UTC)).value == "PREMARKET"
    assert scanner_session(datetime(2026, 8, 11, 15, 0, tzinfo=UTC)).value == "REGULAR"


def test_enabled_sidecar_shares_adapter_coalesces_ticks_and_flushes_session(tmp_path: Path) -> None:
    path = tmp_path / "forward.sqlite3"
    scanner = adapter()
    sidecar = WarriorDesktopSidecar(enabled=True, storage_path=path, clock=lambda: T0)
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("PAPER")
    deliver(scanner, sidecar, quote())
    deliver(scanner, sidecar, trade(2, T0 + timedelta(seconds=1), "10.20"))
    for index in range(3, 20):
        deliver(scanner, sidecar, trade(index, T0 + timedelta(seconds=index), "10.21"))
    store = ForwardCaptureStore(path)
    sidecar._writer.flush()
    assert len(store.records(record_type=CaptureRecordType.DECISION)) == 1
    deliver(scanner, sidecar, trade(20, T0 + timedelta(minutes=1), "10.25"))
    sidecar._writer.flush()
    assert len(store.records(record_type=CaptureRecordType.DECISION)) == 2
    running = sidecar.snapshot()
    assert running.health is WarriorCaptureHealth.RUNNING
    assert running.summary.discovered == 1 and len(running.items) == 1
    sidecar.stop()
    sessions = store.records(record_type=CaptureRecordType.OBSERVATION_SESSION)
    assert tuple(item.payload["action"] for item in sessions) == ("START", "END")
    assert sessions[0].payload["configuration_fingerprint"] == sidecar.configuration_fingerprint
    assert store.records(record_type=CaptureRecordType.DAILY_REPORT)
    assert sidecar.snapshot().health is WarriorCaptureHealth.STOPPED


def test_historical_preload_merges_completed_bars_before_first_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "preload.sqlite3"
    scanner = adapter()

    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=path,
        clock=lambda: T0,
    )
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("PAPER")

    assert sidecar.needs_historical_preload("XYZ")

    historical = tuple(
        SimpleNamespace(
            timestamp=T0 - timedelta(minutes=(8 - index)),
            open=D("10.00"),
            high=D("10.10"),
            low=D("9.90"),
            close=D("10.00"),
            volume=D("1000"),
        )
        for index in range(8)
    )

    inserted = sidecar.preload_historical_bars(
        "XYZ",
        historical,
    )

    assert inserted == 8
    assert not sidecar.needs_historical_preload("XYZ")

    # A quote alone is intentionally insufficient for a scanner
    # observation: it supplies bid/ask but not last price/current volume.
    deliver(scanner, sidecar, quote())

    assert sidecar._writer is not None
    sidecar._writer.flush()

    store = ForwardCaptureStore(path)

    assert store.records(
        record_type=CaptureRecordType.DECISION,
    ) == ()

    # The first trade completes the mandatory scanner state. Warrior should
    # now evaluate immediately using the already-preloaded REST history.
    deliver(
        scanner,
        sidecar,
        trade(2, T0 + timedelta(seconds=1), "10.20"),
    )

    sidecar._writer.flush()

    decisions = store.records(
        record_type=CaptureRecordType.DECISION,
    )

    assert len(decisions) == 1
    assert len(decisions[0].payload["bar_timestamps"]) == 8

    minute_bars = store.records(
        record_type=CaptureRecordType.MINUTE_BAR,
    )

    assert len(minute_bars) == 8

    sidecar.stop()


def test_historical_preload_deduplicates_timestamps_and_skips_current_bar(
    tmp_path: Path,
) -> None:
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=tmp_path / "dedupe.sqlite3",
        clock=lambda: T0,
    )
    sidecar.start("PAPER")

    completed = SimpleNamespace(
        timestamp=T0 - timedelta(minutes=1),
        open=D("10"),
        high=D("10.1"),
        low=D("9.9"),
        close=D("10"),
        volume=D("100"),
    )
    current = SimpleNamespace(
        timestamp=T0,
        open=D("10"),
        high=D("10.2"),
        low=D("9.9"),
        close=D("10.1"),
        volume=D("200"),
    )

    assert sidecar.preload_historical_bars(
        "XYZ",
        (completed, completed, current),
    ) == 1

    assert len(sidecar._bars["XYZ"]) == 1
    assert sidecar._bars["XYZ"][0].timestamp == (
        T0 - timedelta(minutes=1)
    )

    sidecar.stop()


def test_multi_day_restart_appends_and_capture_failure_does_not_escape_stream(tmp_path: Path) -> None:
    path = tmp_path / "multi.sqlite3"
    times = iter((T0, T0, T0 + timedelta(days=1), T0 + timedelta(days=1)))
    sidecar = WarriorDesktopSidecar(enabled=True, storage_path=path, clock=lambda: next(times))
    sidecar.start("PAPER")
    sidecar.stop()
    sidecar.start("PAPER")
    sidecar.stop()
    store = ForwardCaptureStore(path)
    sessions = store.records(record_type=CaptureRecordType.OBSERVATION_SESSION)
    assert len(sessions) == 4
    assert {item.payload["trading_date"] for item in sessions} == {"2026-08-11", "2026-08-12"}

    scanner = adapter()
    failing = WarriorDesktopSidecar(enabled=True, storage_path=tmp_path / "failure.sqlite3", clock=lambda: T0)
    failing.bind_scanner_adapter(scanner)
    failing.start("PAPER")
    deliver(scanner, failing, quote())
    assert failing._writer is not None
    failing._writer._fatal = RuntimeError("storage failed")
    # Capture contains the exception and degrades only its own health.
    deliver(scanner, failing, trade(2, T0 + timedelta(seconds=1), "10.20"))
    assert failing.snapshot().health is WarriorCaptureHealth.DEGRADED
    failing._writer._fatal = None
    failing.stop()


def test_sidecar_stop_identifies_capture_writer_drain_failure(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=tmp_path / "stop-failure.sqlite3",
        clock=lambda: T0,
    )
    sidecar.start("PAPER")
    writer = sidecar._writer
    assert writer is not None
    original_flush = writer.flush

    def fail_flush() -> None:
        raise RuntimeError("synthetic writer drain sentinel")

    monkeypatch.setattr(writer, "flush", fail_flush)
    with caplog.at_level("ERROR", logger="atlas.runtime"):
        with pytest.raises(RuntimeError, match="synthetic writer drain sentinel"):
            sidecar.stop()

    assert sidecar.snapshot().health is WarriorCaptureHealth.DEGRADED
    assert "lifecycle_phase=shadow/capture writer drain" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text

    monkeypatch.setattr(writer, "flush", original_flush)
    writer.close()


def _hod_bars() -> tuple[MinuteBar, ...]:
    def item(i, o, h, low, close, volume="100"):
        return MinuteBar("XYZ", T0 + timedelta(minutes=i), D(o), D(h), D(low), D(close), D(volume))
    return (
        item(0, "9.7", "9.9", "9.6", "9.8"), item(1, "9.8", "10", "9.75", "9.9"),
        item(2, "9.9", "9.99", "9.8", "9.92"), item(3, "9.92", "10", "9.85", "9.95"),
        item(4, "9.96", "10.2", "9.94", "10.10", "300"),
    )


def test_warrior_focus_maps_trigger_stop_provenance_and_blocked_reasons() -> None:
    scanner = adapter()
    scanner.consume(quote(T0 + timedelta(minutes=20)))
    scanner.consume(trade(2, T0 + timedelta(minutes=20), "10.20"))
    observation = scanner.observation_for("XYZ")
    assert observation is not None
    observation = replace(observation, bid=D("9"), ask=D("11"))
    runtime = WarriorMomentumRuntime()
    candidate, signal = runtime.assess_entry(runtime.discover(observation, _hod_bars(), session="REGULAR"))
    assert signal is None and candidate.setup is not None
    view = format_warrior_paper(WarriorPaperSnapshot(
        True, WarriorCaptureHealth.RUNNING, "fp",
        (WarriorFocusItem(candidate, FloatProvenance.MARKET_CAP_PRICE_PROXY,
                          candidate.setup.trigger, candidate.setup.stop_price,
                          ("spread",)),),
    ))
    row = view.focus.rows[0]
    assert row.entry_trigger != "--" and row.stop_price != "--"
    assert row.float_provenance == "MCAP/PRICE PROXY"
    assert row.blocking_reasons == "Spread is too wide"
    assert row.strategy_status == "ENTRY BLOCKED"


def _session_record(action: str, at: datetime, fingerprint: str) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.OBSERVATION_SESSION, "WARRIOR_MOMENTUM_V1", at,
        {"action": action, "strategy_version": "WARRIOR_MOMENTUM_V1",
         "schema_version": 1, "trading_date": at.date().isoformat(),
         "capture_start": at, "capture_end": at if action == "END" else None,
         "environment": "PAPER", "configuration_fingerprint": fingerprint,
         "observation_run_key": f"run-{fingerprint}"},
        identity_parts=(action, fingerprint),
    )


def _trade_records(at: datetime, fingerprint: str, realized: str):
    entry = CaptureRecord.create(CaptureRecordType.PAPER_FILL, "XYZ", at + timedelta(minutes=1), {
        "action": "ENTRY", "setup": "BULL_FLAG", "fill_price": "10",
        "price": "10", "momentum_score": "80", "relative_volume": "8",
        "float_shares": "6000000", "float_provenance": "MARKET_CAP_PRICE_PROXY",
        "catalyst_state": "TRUE", "session": "REGULAR",
    }, identity_parts=(fingerprint, "ENTRY"))
    exit_record = CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION, "XYZ", at + timedelta(minutes=2),
        {"to": "PAPER_EXIT", "realized_r": realized, "mae_r": "-0.5",
         "mfe_r": "1.5", "hold_seconds": "60", "reason_codes": []},
        identity_parts=(fingerprint, "EXIT"),
    )
    return entry, exit_record


def test_cumulative_reports_separate_incompatible_fingerprints_and_label_maturity(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "reports.sqlite3")
    records = []
    for index, (fingerprint, result) in enumerate((("fp-a", "1"), ("fp-b", "-1"))):
        at = T0 + timedelta(days=index)
        records.extend((_session_record("START", at, fingerprint),
                        *_trade_records(at, fingerprint, result),
                        _session_record("END", at + timedelta(minutes=3), fingerprint)))
    store.append_batch(tuple(records))
    reports = build_cumulative_reports(store)
    assert tuple(item.configuration_fingerprint for item in reports) == ("fp-a", "fp-b")
    assert tuple(item.paper_trades for item in reports) == (1, 1)
    assert all(item.maturity is EvidenceMaturity.EARLY_SAMPLE for item in reports)
    assert evidence_maturity(0) is EvidenceMaturity.NO_TRADES
    assert evidence_maturity(20) is EvidenceMaturity.DEVELOPING_SAMPLE
    assert evidence_maturity(100) is EvidenceMaturity.MEANINGFUL_SAMPLE


def test_execution_firewall_has_no_broker_mutation_surface(tmp_path: Path) -> None:
    sidecar = WarriorDesktopSidecar(enabled=True, storage_path=tmp_path / "firewall.sqlite3")
    forbidden = {"submit_order", "replace_order", "cancel_order", "authorize_live"}
    assert forbidden.isdisjoint(dir(sidecar))
    assert WarriorMomentumRuntime.authorize_live(object()) is False
