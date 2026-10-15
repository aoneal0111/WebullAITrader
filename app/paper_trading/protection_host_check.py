"""Synthetic subprocess takeover check; never opens the user's ledger or feed."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
import multiprocessing
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from types import SimpleNamespace

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.gui.runtime_ownership import acquire_runtime_ownership
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, ResumePayload
from app.operations_core import OperationsBus
from app.paper_trading.command_composition import create_paper_trading_command_composition, PAPER_ACCOUNT_ID
from app.paper_trading.protection_host import PaperProtectionHost
from app.paper_trading.protection_feed import PaperProtectionFeed
from app.paper_trading.protection_monitor import PaperProtectionMonitor
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge

T0 = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def quote(at, *, bid="9.40", size="10"):
    return MarketEvent(1, at, "DEMO", "synthetic-protection-check", MarketEventType.QUOTE,
                       QuotePayload(D(bid), D(bid)+D("0.01"), D(size), D(size)))


def seed(path, *, stop=True, partial=True, lifecycle="WARRIOR_MOMENTUM_V1|DEMO|SYNTHETIC"):
    projection = create_runtime_projection_pipeline(operations_bus=OperationsBus(), account_id=PAPER_ACCOUNT_ID)
    quantity = lambda symbol: sum((D(p.quantity) for p in projection.position_projection.snapshot.positions
                                   if p.symbol == symbol), D(0))
    commands = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=projection.sink,
        position_quantity_source=quantity, clock=lambda: T0,
    )
    try:
        bridge = AutonomousPaperExecutionBridge(commands.trading_service, commands.order_command_factory,
            order_book=commands.order_book, position_quantity_source=quantity)
        signal = SimpleNamespace(symbol="DEMO", entry_trigger=D("10"), stop_price=D("9.50"),
                                 lifecycle_id=lifecycle,
                                 strategy_id="QUICK_SCALPER" if lifecycle.startswith("QUICK_SCALPER|") else "WARRIOR_MOMENTUM_V1")
        assert bridge.submit_entry(signal, 20 if partial else 10, D("10"))
        commands.gateway.process_market_event(quote(T0, bid="9.99"))
        assert quantity("DEMO") == D("10")
        if stop:
            assert bridge.ensure_exit("DEMO", 10, D("9.50"), "STOP", lifecycle).protection_active
    finally:
        commands.close()


def _worker(path, pipe):
    host = None
    monitor = None
    try:
        now = T0 + timedelta(seconds=2)
        host = PaperProtectionHost(path, environment="PAPER", clock=lambda: now)
        pipe.send({"ready": True, **host.snapshot()})
        if pipe.recv() != "PROCESS_SYNTHETIC_QUOTE":
            raise ValueError("Unexpected check command")
        mono = [100.0]
        feed = PaperProtectionFeed(host, source="synthetic-protection-check",
                                   silence_seconds=5, clock=lambda: mono[0])
        monitor = PaperProtectionMonitor(feed, poll_seconds=.01)
        monitor.start()
        old = feed.connect()
        now += timedelta(seconds=1)
        feed.process(old, MarketEvent(2, now, "DEMO", "synthetic-protection-check",
                                     MarketEventType.RESUME, ResumePayload("synthetic")))
        now += timedelta(seconds=5)
        mono[0] += 5
        deadline = monotonic() + 2
        pause = Event()
        while monitor.snapshot()["last_feed_state"] != "FEED_SILENT" and monotonic() < deadline:
            pause.wait(.005)
        silence_paused = (monitor.snapshot()["last_feed_state"] == "FEED_SILENT"
                          and host._quantity("DEMO") == 10)
        token = feed.connect()
        now += timedelta(seconds=1)
        old_blocked = feed.process(old, quote(now)) == "OLD_CONNECTION"
        status_required = feed.process(token, quote(now)) == "HALTED"
        now += timedelta(seconds=1)
        feed.process(token, MarketEvent(3, now, "DEMO", "synthetic-protection-check",
                                       MarketEventType.RESUME, ResumePayload("synthetic")))
        now += timedelta(seconds=1)
        outcome = feed.process(token, quote(now))
        monitor_stopped = monitor.close()
        closed_callbacks_blocked = feed.process(token, quote(now)) == "OLD_CONNECTION"
        pipe.send({"outcome": outcome, **host.snapshot(),
                   "silence_paused": silence_paused, "old_connection_blocked": old_blocked,
                   "reconnect_requires_status": status_required,
                   "periodic_silence_check": silence_paused,
                   "monitor_stopped": monitor_stopped,
                   "closed_callbacks_blocked": closed_callbacks_blocked,
                   "qt_widgets_imported": "PySide6.QtWidgets" in sys.modules,
                   "working_buys": sum(o.request.side.value == "BUY" for o in host._commands.order_book.open_orders())})
    except Exception as exc:
        pipe.send({"error": type(exc).__name__})
    finally:
        if monitor is not None:
            monitor.close()
        if host is not None:
            host.close()
        pipe.close()


def synthetic_check():
    context = multiprocessing.get_context("spawn")
    with TemporaryDirectory(prefix="atlas-protection-host-") as folder:
        path = Path(folder) / "paper-execution.sqlite3"
        seed(path)
        parent, child = context.Pipe()
        worker = context.Process(target=_worker, args=(str(path), child))
        worker.start()
        child.close()
        try:
            if not parent.poll(15):
                raise RuntimeError("Protection child did not start")
            ready = parent.recv()
            if not ready.get("ready"):
                raise RuntimeError("Protection child failed")
            competing = acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock"))
            duplicate_blocked = competing is None
            if competing is not None:
                competing.release()
            parent.send("PROCESS_SYNTHETIC_QUOTE")
            if not parent.poll(15):
                raise RuntimeError("Protection child did not finish")
            result = parent.recv()
            worker.join(5)
            passed = (duplicate_blocked and worker.exitcode == 0
                      and result.get("outcome") == "PROCESSED" and result.get("positions") == 0
                      and result.get("qt_widgets_imported") is False
                      and result.get("silence_paused") is True
                      and result.get("old_connection_blocked") is True
                      and result.get("reconnect_requires_status") is True
                      and result.get("monitor_stopped") is True
                      and result.get("closed_callbacks_blocked") is True
                      and result.get("working_buys") == 0)
            return dict(scope="SYNTHETIC_HEADLESS_PROTECTION_NOT_LIVE_FEED_OR_PERFORMANCE",
                        passed=passed, separate_process=True, original_command_runtime_closed=True,
                        duplicate_owner_blocked=duplicate_blocked, positions_remaining=result.get("positions"),
                        qt_widgets_imported=result.get("qt_widgets_imported"),
                        silence_paused=result.get("silence_paused"),
                        old_connection_blocked=result.get("old_connection_blocked"),
                        reconnect_requires_status=result.get("reconnect_requires_status"),
                        periodic_silence_check=result.get("periodic_silence_check"),
                        monitor_stopped=result.get("monitor_stopped"),
                        closed_callbacks_blocked=result.get("closed_callbacks_blocked"),
                        entries_enabled=False, live_feed_validated=False, network_requests=0)
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            parent.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true", required=True)
    parser.parse_args(argv)
    report = synthetic_check()
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
