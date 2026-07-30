from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.broker_plugins.factory import create_broker_runtime
from app.composition.broker_order_projection import (
    create_broker_orders_publisher,
)
from app.composition.broker_position_projection import (
    create_broker_positions_publisher,
)
from app.composition.operational_lifecycle import (
    OperationalRuntimeSession,
)
from app.composition.operational_runtime import (
    OperationalRuntimeComposition,
    create_operational_runtime_composition,
)
from app.configuration.loader import load_configuration
from app.configuration.models import TradingEnvironment
from app.live_execution.account_polling import poll_broker_account
from app.live_execution.broker_factory import build_webull_broker
from app.live_execution.recovery import reconcile_startup
from app.operations_core import OperationsBus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic_decimal() -> Decimal:
    return Decimal(str(time.monotonic()))


def sleep_decimal(seconds: Decimal) -> None:
    time.sleep(float(seconds))


def build_broker(configuration):
    """Create the configured broker through the broker-plugin runtime."""

    runtime = create_broker_runtime(
        provider=configuration.broker_provider,
        configuration=configuration,
        webull_broker_factory=build_webull_broker,
    )

    return runtime.execution


def build_operational_runtime(
    configuration,
) -> OperationalRuntimeComposition:
    """Compose operational infrastructure without starting its lifecycle."""

    return create_operational_runtime_composition(
        configuration=configuration,
        clock=utc_now,
        broker_factory=build_broker,
    )


def ensure_parent_directories(configuration) -> None:
    paths = (
        configuration.authorization_database_path,
        configuration.execution_database_path,
        configuration.market_event_database_path,
        configuration.emergency_stop_database_path,
    )

    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def validate_environment(configuration) -> None:
    if configuration.environment is TradingEnvironment.LIVE:
        raise RuntimeError(
            "operational_main currently refuses LIVE mode; "
            "complete sandbox validation first"
        )

    integration_mode = os.environ.get(
        "BROKER_INTEGRATION_MODE",
        "",
    ).upper()

    if integration_mode not in {"SANDBOX", "PAPER", "LIVE_READ_ONLY"}:
        raise RuntimeError(
            "BROKER_INTEGRATION_MODE must be SANDBOX, PAPER, "
            "or LIVE_READ_ONLY"
        )

    if integration_mode == "LIVE_READ_ONLY":
        if (
            os.environ.get(
                "ALLOW_LIVE_INTEGRATION_MUTATIONS",
                "false",
            ).lower()
            == "true"
        ):
            raise RuntimeError(
                "live mutations cannot be enabled by this entry point"
            )


def check_startup() -> int:
    configuration = load_configuration()
    validate_environment(configuration)
    ensure_parent_directories(configuration)

    runtime = build_operational_runtime(configuration)
    authorization_registry = runtime.authorization_registry
    execution_journal = runtime.execution_journal
    market_store = runtime.market_store
    emergency_stop = runtime.emergency_stop
    broker = runtime.broker

    with OperationalRuntimeSession(runtime) as session:
        print(
            f"Environment: {configuration.environment.value}",
            flush=True,
        )
        print(
            "Live trading enabled: "
            f"{configuration.live_trading_enabled}",
            flush=True,
        )
        print(
            "Allowed symbols: "
            f"{', '.join(configuration.allowed_symbols) or '(none)'}",
            flush=True,
        )

        stop_state = emergency_stop.state()
        print(
            "Emergency stop: "
            f"{'ACTIVE' if stop_state.enabled else 'CLEARED'} "
            f"({stop_state.reason})",
            flush=True,
        )

        # Verify durable services before touching the network.
        authorization_registry.authorizations
        execution_journal.pending
        market_store.reachable()
        emergency_stop.reachable()

        print("Durable stores: reachable", flush=True)
        print("Connecting to configured Webull environment.", flush=True)

        session.connect()

        snapshot = poll_broker_account(broker, clock=utc_now)
        account = snapshot.account
        cash = snapshot.cash
        positions = snapshot.positions
        orders = snapshot.orders

        print(f"Account: {account}", flush=True)
        print(f"Cash: {cash}", flush=True)
        print(f"Positions: {positions}", flush=True)
        print(f"Open orders: {orders}", flush=True)

        results = reconcile_startup(
            execution_journal,
            broker,
            authorization_registry,
            utc_now(),
        )

        unresolved = tuple(
            item
            for item in execution_journal.pending
            if getattr(item.state, "value", str(item.state))
            == "UNRESOLVED"
        )

        print(
            f"Reconciliation records processed: {len(results)}",
            flush=True,
        )
        print(
            f"Pending execution mutations: "
            f"{len(execution_journal.pending)}",
            flush=True,
        )
        print(
            f"Unresolved execution mutations: {len(unresolved)}",
            flush=True,
        )

        if unresolved:
            print(
                "CHECK FAILED: unresolved execution mutations exist.",
                file=sys.stderr,
                flush=True,
            )
            return 2

        if not emergency_stop.state().enabled:
            print(
                "WARNING: emergency stop is currently cleared.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                "Safety status: order submissions remain blocked.",
                flush=True,
            )

        print("SANDBOX STARTUP CHECK PASSED", flush=True)
        return 0



def _unresolved_mutations(execution_journal) -> tuple[object, ...]:
    return tuple(
        item
        for item in execution_journal.pending
        if getattr(item.state, "value", str(item.state)) == "UNRESOLVED"
    )


def _validate_observation_mode(configuration, emergency_stop) -> None:
    if configuration.environment is TradingEnvironment.LIVE:
        raise RuntimeError("observation mode refuses LIVE environment")
    if configuration.live_trading_enabled:
        raise RuntimeError(
            "observation mode requires LIVE_TRADING_ENABLED=false"
        )
    if not emergency_stop.state().enabled:
        raise RuntimeError(
            "observation mode requires the emergency stop to remain active"
        )


def run_observation(
    *,
    max_cycles: int | None = None,
    interval_seconds: Decimal | None = None,
    operations_bus: OperationsBus | None = None,
) -> int:
    """Continuously reconcile and display broker state without mutations."""

    if max_cycles is not None and max_cycles <= 0:
        raise ValueError("max_cycles must be positive")
    if interval_seconds is not None and interval_seconds < 0:
        raise ValueError("interval_seconds must be nonnegative")

    configuration = load_configuration()
    validate_environment(configuration)
    ensure_parent_directories(configuration)

    if operations_bus is not None and not isinstance(
        operations_bus, OperationsBus
    ):
        raise TypeError("operations_bus must be an OperationsBus")

    runtime = build_operational_runtime(configuration)
    authorization_registry = runtime.authorization_registry
    execution_journal = runtime.execution_journal
    market_store = runtime.market_store
    emergency_stop = runtime.emergency_stop
    broker = runtime.broker
    publish_orders = (
        create_broker_orders_publisher(operations_bus)
        if operations_bus is not None
        else None
    )
    publish_positions = (
        create_broker_positions_publisher(operations_bus)
        if operations_bus is not None
        else None
    )
    with OperationalRuntimeSession(runtime) as session:
        _validate_observation_mode(configuration, emergency_stop)

        # Verify all durable dependencies before network access.
        authorization_registry.authorizations
        execution_journal.pending
        market_store.reachable()
        emergency_stop.reachable()

        print("OBSERVATION MODE", flush=True)
        print(
            f"Environment: {configuration.environment.value}",
            flush=True,
        )
        print("Order submission: DISABLED", flush=True)
        print("Emergency stop: ACTIVE", flush=True)

        session.connect()

        reconcile_startup(
            execution_journal,
            broker,
            authorization_registry,
            utc_now(),
        )
        unresolved = _unresolved_mutations(execution_journal)
        if execution_journal.pending or unresolved:
            raise RuntimeError(
                "observation mode refuses pending or unresolved "
                "execution mutations"
            )

        delay = (
            interval_seconds
            if interval_seconds is not None
            else Decimal(configuration.reconciliation_interval_seconds)
        )
        cycle = 0

        while max_cycles is None or cycle < max_cycles:
            _validate_observation_mode(configuration, emergency_stop)

            snapshot = poll_broker_account(broker, clock=utc_now)
            account = snapshot.account
            cash = snapshot.cash
            positions = snapshot.positions
            orders = snapshot.orders
            observed_at = snapshot.observed_at
            if publish_orders is not None:
                publish_orders(orders, observed_at)
            if publish_positions is not None:
                publish_positions(
                    positions,
                    account,
                    cash,
                    observed_at,
                )

            cycle += 1
            timestamp = observed_at.isoformat()
            print(
                f"Observation cycle {cycle} at {timestamp}",
                flush=True,
            )
            print(f"Account: {account}", flush=True)
            print(f"Cash: {cash}", flush=True)
            print(f"Positions: {positions}", flush=True)
            print(f"Open orders: {orders}", flush=True)

            reconcile_startup(
                execution_journal,
                broker,
                authorization_registry,
                utc_now(),
            )
            if execution_journal.pending or _unresolved_mutations(
                execution_journal
            ):
                raise RuntimeError(
                    "execution mutation appeared during observation mode"
                )

            if max_cycles is None or cycle < max_cycles:
                sleep_decimal(delay)

        print("OBSERVATION RUN COMPLETED", flush=True)
        return 0



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Webull operational startup and observation service"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate durable state and sandbox broker connectivity",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="run continuous read-only broker observation",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="stop observation mode after this many cycles",
    )
    parser.add_argument(
        "--interval-seconds",
        type=Decimal,
        default=None,
        help="override the configured observation interval",
    )

    args = parser.parse_args()

    if args.check:
        if args.max_cycles is not None or args.interval_seconds is not None:
            parser.error(
                "--max-cycles and --interval-seconds require --run"
            )
        return check_startup()

    return run_observation(
        max_cycles=args.max_cycles,
        interval_seconds=args.interval_seconds,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"Operational startup failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
