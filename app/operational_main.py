from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.authorization.registry import AuthorizationRegistry
from app.configuration.loader import load_configuration
from app.configuration.models import TradingEnvironment
from app.live_execution.recovery import (
    DurableExecutionJournal,
    reconcile_startup,
)
from app.live_execution.webull_adapter import WebullAdapter
from app.market_data.durable_store import DurableMarketEventStore
from app.operations.credentials import EnvironmentCredentialProvider
from app.operations.emergency_stop import EmergencyStopStore
from app.webull.configuration import (
    ReconnectPolicy,
    RetryPolicy,
    WebSocketSettings,
    WebullConfiguration,
)
from app.webull.http_client import UrllibHttpBackend, WebullHttpClient
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.signing import WebullRequestSigner
from app.webull.transport import WebullBrokerTransport


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic_decimal() -> Decimal:
    return Decimal(str(time.monotonic()))


def sleep_decimal(seconds: Decimal) -> None:
    time.sleep(float(seconds))


class ConsoleSink:
    def emit(self, record: object) -> None:
        print(record, flush=True)


class SignedAuthentication:
    """Authentication interface expected by WebullBrokerTransport."""

    def __init__(self, signer: WebullRequestSigner) -> None:
        self.signer = signer

    def headers(
        self,
        method: str,
        path: str,
        query: tuple[tuple[str, object], ...],
        body: bytes | None,
    ) -> dict[str, str]:
        return self.signer.headers(method, path, query, body)

    def verify(self) -> bool:
        # The authenticated account-list request verifies the credentials.
        return True


def ensure_parent_directories(configuration) -> None:
    paths = (
        configuration.authorization_database_path,
        configuration.execution_database_path,
        configuration.market_event_database_path,
        configuration.emergency_stop_database_path,
    )

    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def build_broker(configuration) -> WebullAdapter:
    credentials = EnvironmentCredentialProvider(os.environ)
    transport_logger = StructuredLogger(ConsoleSink())

    webull_configuration = WebullConfiguration(
        api_endpoint=configuration.api_base_url.rstrip("/"),
        account_id=configuration.account_id,
        timeout_seconds=Decimal("10"),
        retry_policy=RetryPolicy(
            maximum_attempts=3,
            initial_backoff_seconds=Decimal("1"),
            multiplier=Decimal("2"),
            maximum_backoff_seconds=Decimal("5"),
        ),
        reconnect_policy=ReconnectPolicy(
            maximum_attempts=3,
            backoff_seconds=Decimal("1"),
        ),
        websocket=WebSocketSettings(
            endpoint=configuration.stream_url,
        ),
    )

    signer = WebullRequestSigner(
        credentials=credentials,
        host=webull_configuration.api_endpoint,
        clock=utc_now,
        nonce_provider=lambda: uuid.uuid4().hex,
    )

    authentication = SignedAuthentication(signer)

    limiter = DeterministicRateLimiter(
        RateLimit(
            requests=10,
            window_seconds=Decimal("1"),
        ),
        monotonic_decimal,
        sleep_decimal,
    )

    http_client = WebullHttpClient(
        endpoint=webull_configuration.api_endpoint,
        timeout=webull_configuration.timeout_seconds,
        retry_policy=webull_configuration.retry_policy,
        backend=UrllibHttpBackend(),
        auth=authentication,
        limiter=limiter,
        sleeper=sleep_decimal,
        logger=transport_logger,
    )

    transport = WebullBrokerTransport(
        webull_configuration,
        http_client,
        authentication,
        transport_logger,
        utc_now,
    )

    return WebullAdapter(transport)


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

    authorization_registry = AuthorizationRegistry(
        configuration.authorization_database_path
    )
    execution_journal = DurableExecutionJournal(
        configuration.execution_database_path
    )
    market_store = DurableMarketEventStore(
        configuration.market_event_database_path
    )
    emergency_stop = EmergencyStopStore(
        configuration.emergency_stop_database_path,
        utc_now,
    )
    broker = build_broker(configuration)

    connected = False

    try:
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

        broker.connect()
        connected = True

        account = broker.get_account()
        cash = broker.get_cash()
        positions = broker.get_positions()
        orders = broker.get_orders()

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
            item for item in execution_journal.pending
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

    finally:
        if connected:
            broker.disconnect()

        market_store.close()
        emergency_stop.close()
        authorization_registry.close()

        close_journal = getattr(execution_journal, "close", None)
        if callable(close_journal):
            close_journal()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Webull operational startup validation"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate durable state and sandbox broker connectivity",
    )
    args = parser.parse_args()

    if not args.check:
        parser.error(
            "Only --check is currently supported. "
            "Continuous trading remains disabled."
        )

    return check_startup()


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
