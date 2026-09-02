"""Controlled headless TEST/PAPER Phase 2A.2 soak harness."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import monotonic, sleep

from dotenv import load_dotenv

from app.composition.desktop import create_desktop_composition
from app.configuration import load_configuration
from app.gui.app import configured_paper_persistence_path
from app.logging_config import configure_logging
from app.performance_diagnostics import performance_diagnostics


def _json(kind: str, **values: object) -> None:
    print(json.dumps({"kind": kind, **values}, default=str, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=1500)
    parser.add_argument("--research-store", type=Path, required=True)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        raise ValueError("duration must be positive")
    load_dotenv(override=False)
    os.environ["TRADE_INTELLIGENCE_PATH"] = str(args.research_store.resolve())
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    configuration = load_configuration()
    if configuration.live_trading_enabled:
        raise RuntimeError("controlled research soak requires LIVE disabled")
    if configuration.environment.value not in {"TEST", "PAPER"}:
        raise RuntimeError("controlled research soak requires TEST/PAPER")
    if not configuration.trade_intelligence_enabled:
        raise RuntimeError("Trade Intelligence must be enabled")
    configure_logging(configuration.log_level)
    composition = create_desktop_composition(
        paper_persistence_path=configured_paper_persistence_path(),
    )
    started_at = datetime.now(UTC)
    _json(
        "soak_start", pid=os.getpid(), started_at=started_at,
        duration_seconds=args.duration_seconds,
        trading_environment=configuration.environment.value,
        market_data_environment=(
            None if configuration.market_data is None
            else configuration.market_data.environment.value
        ),
        paper_enabled=configuration.warrior_forward_paper_enabled,
        live_enabled=configuration.live_trading_enabled,
        research_store=str(configuration.trade_intelligence_path),
    )
    if not composition.runtime_service.start():
        raise RuntimeError("runtime did not start")
    started = monotonic()
    try:
        while monotonic() - started < args.duration_seconds:
            sleep(min(30, args.duration_seconds - (monotonic() - started)))
            metrics = performance_diagnostics.snapshot()
            _json(
                "soak_telemetry", elapsed_seconds=round(monotonic() - started, 3),
                **asdict(metrics),
            )
    finally:
        composition.runtime_service.stop("Controlled Phase 2A.2 soak complete.")
        runtime_stopped = composition.runtime_service.wait(30)
        observer = composition.trade_intelligence_observer
        composition.close(timeout_seconds=30)
        metrics = performance_diagnostics.snapshot()
        discovery = None if observer is None else observer.discovery_telemetry()
        worker = None if observer is None else observer.metrics()
        _json(
            "soak_final", stopped_at=datetime.now(UTC),
            runtime_stopped=runtime_stopped,
            performance=asdict(metrics),
            discovery=None if discovery is None else asdict(discovery),
            worker=None if worker is None else asdict(worker),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
