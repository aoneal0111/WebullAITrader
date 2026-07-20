from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from webull.core.client import ApiClient
from webull.core.http.initializer.token.token_manager import TokenManager
from webull.trade.trade_client import TradeClient
from decimal import Decimal
from uuid import uuid4

from app.broker_protocol.models import (
    BrokerOrderRequest,
    BrokerOrderType,
    BrokerSide,
    TimeInForce,
    TradingSession,
)
from app.live_execution.webull_adapter import WebullAdapter
from app.operations.credentials import EnvironmentCredentialProvider
from app.webull.configuration import (
    ReconnectPolicy,
    RetryPolicy,
    WebSocketSettings,
    WebullConfiguration,
)
from app.webull.trading_sessions import MarketSessionClosedError
from app.webull.http_client import UrllibHttpBackend, WebullHttpClient
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.signing import WebullRequestSigner
from app.webull.transport import WebullBrokerTransport
from app.webull.errors import BrokerRejectionError
API_KEY = os.environ.get("WEBULL_API_KEY", "").strip()
API_SECRET = os.environ.get("WEBULL_API_SECRET", "").strip()
ACCOUNT_ID = os.environ.get("WEBULL_ACCOUNT_ID", "").strip()

SDK_ENDPOINT = os.environ.get(
    "WEBULL_API_ENDPOINT",
    "api.sandbox.webull.com",
).strip()
SDK_ENDPOINT = SDK_ENDPOINT.removeprefix("https://").removeprefix("http://")
SDK_ENDPOINT = SDK_ENDPOINT.rstrip("/")

HTTP_ENDPOINT = f"https://{SDK_ENDPOINT}"

WEBSOCKET_ENDPOINT = os.environ.get(
    "WEBULL_WEBSOCKET_ENDPOINT",
    "wss://data-api.sandbox.webull.com/mqtt",
).strip()

REGION_ID = os.environ.get("WEBULL_REGION_ID", "us").strip() or "us"
RUN_ORDER_TEST = os.environ.get(
    "WEBULL_RUN_ORDER_TEST",
    "",
).strip().lower() in {"1", "true", "yes"}


def require_environment() -> None:
    missing: list[str] = []

    if not API_KEY:
        missing.append("WEBULL_API_KEY")
    if not API_SECRET:
        missing.append("WEBULL_API_SECRET")
    if not ACCOUNT_ID:
        missing.append("WEBULL_ACCOUNT_ID")

    if missing:
        print("Missing required environment variables:")
        for name in missing:
            print(f"  - {name}")
        print("\nSet the variables in PowerShell before running this script.")
        raise SystemExit(1)

    placeholders = {"NEW_APP_KEY", "NEW_APP_SECRET"}
    if API_KEY in placeholders or API_SECRET in placeholders:
        raise SystemExit(
            "WEBULL_API_KEY or WEBULL_API_SECRET still contains a placeholder."
        )


class ConsoleLogSink:
    def emit(self, record: dict[str, object]) -> None:
        print(record)


logger = StructuredLogger(ConsoleLogSink())


class SignedAuthentication:
    """Authentication adapter used by WebullBrokerTransport."""

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
        return True


def decimal_clock() -> Decimal:
    return Decimal(str(time.monotonic()))


def decimal_sleep(seconds: Decimal) -> None:
    time.sleep(float(seconds))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def nonce() -> str:
    return uuid.uuid4().hex


def print_response(label: str, response: Any) -> Any:
    print(f"\n{label}")
    print("-" * len(label))

    status_code = getattr(response, "status_code", None)
    print("Status:", status_code)

    try:
        body = response.json()
    except Exception:
        body = getattr(response, "text", repr(response))

    print("Body:", body)
    return body


def extract_accounts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]

    if not isinstance(value, dict):
        return []

    for key in ("data", "items", "accounts", "account_list"):
        nested = value.get(key)

        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        if isinstance(nested, dict):
            for nested_key in ("items", "accounts", "list"):
                nested_items = nested.get(nested_key)
                if isinstance(nested_items, list):
                    return [
                        item
                        for item in nested_items
                        if isinstance(item, dict)
                    ]

    if value.get("account_id") is not None:
        return [value]

    return []


def create_credential_provider() -> EnvironmentCredentialProvider:
    try:
        return EnvironmentCredentialProvider()
    except TypeError:
        return EnvironmentCredentialProvider(
            "WEBULL_API_KEY",
            "WEBULL_API_SECRET",
        )


def run_sdk_checks(
    sdk_client: ApiClient,
    configured_account_id: str,
) -> None:
    trade_client = TradeClient(sdk_client)

    account_response = trade_client.account_v2.get_account_list()
    account_body = print_response("Direct SDK account list", account_response)
    accounts = extract_accounts(account_body)

    print("\nConfigured account ID:", configured_account_id)

    if accounts:
        print("Account IDs returned by SDK:")
        for account in accounts:
            print(
                " ",
                account.get("account_id"),
                account.get("account_type"),
                account.get("account_class"),
            )
    else:
        print("No account records could be extracted from the response.")

    matching_account = next(
        (
            account
            for account in accounts
            if str(account.get("account_id")) == configured_account_id
        ),
        None,
    )

    if accounts and matching_account is None:
        print(
            "\nWARNING: WEBULL_ACCOUNT_ID does not match an account "
            "returned by the SDK."
        )

    balance_response = trade_client.account_v2.get_account_balance(
        configured_account_id
    )
    print_response("Direct SDK balance", balance_response)

    position_response = trade_client.account_v2.get_account_position(
        configured_account_id
    )
    print_response("Direct SDK positions", position_response)


def run_optional_order_test(adapter) -> None:
    if os.getenv("WEBULL_RUN_ORDER_TEST", "0") != "1":
        print(
            "\nOrder test skipped. "
            "Set WEBULL_RUN_ORDER_TEST=1 to enable it."
        )
        return

    print("\nOrder test enabled.")

    order = BrokerOrderRequest(
        client_order_id=f"smoke-{uuid4().hex[:20]}",
        symbol="AAPL",
        side=BrokerSide.BUY,
        order_type=BrokerOrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("150.00"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        trading_session=TradingSession.AUTO,
    )

    print("Submitting sandbox order:")
    print(order)

    try:
        result = adapter.submit_order(order)

        print("Order result:")
        print(result)

    except MarketSessionClosedError as exc:
        print(f"Order submission skipped: {exc}")
        print()
        print("Transport test PASSED")
        print(
            "TradingSession.AUTO correctly detected that "
            "US equity markets are closed."
        )
        return

    except BrokerRejectionError as exc:
        if (
            "OAUTH_OPENAPI_CAN_NOT_TRADING_FOR_NON_TRADING_HOURS"
            in str(exc)
        ):
            print()
            print("Transport test PASSED")
            print(
                "Webull accepted the request format but rejected execution "
                "because the market is closed."
            )
            return

        raise

def main() -> None:
    require_environment()

    # Official Webull SDK
    sdk_client = ApiClient(
        API_KEY,
        API_SECRET,
        REGION_ID,
    )

    sdk_client.add_endpoint(
        REGION_ID,
        SDK_ENDPOINT,
    )

    print("\nInitializing Webull access token...")

    token_manager = TokenManager()
    access_token = token_manager.init_token(sdk_client)

    if not access_token:
        raise RuntimeError(
            "Webull SDK did not return an access token."
        )

    print("Access token initialized.")

    try:
        run_sdk_checks(
            sdk_client,
            ACCOUNT_ID,
        )
    except Exception as exc:
        print("\nDirect SDK check failed:")
        print(type(exc).__name__, str(exc))

    # Custom transport
    credentials = EnvironmentCredentialProvider(os.environ)

    configuration = WebullConfiguration(
        HTTP_ENDPOINT,
        ACCOUNT_ID,
        Decimal("10"),
        RetryPolicy(
            3,
            Decimal("1"),
            Decimal("2"),
            Decimal("5"),
        ),
        ReconnectPolicy(
            3,
            Decimal("1"),
        ),
        WebSocketSettings(
            WEBSOCKET_ENDPOINT,
        ),
    )

    signer = WebullRequestSigner(
        credentials,
        HTTP_ENDPOINT,
        utc_now,
        nonce,
        access_token_provider=lambda: access_token,
    )

    authentication = SignedAuthentication(signer)

    limiter = DeterministicRateLimiter(
        RateLimit(
            requests=10,
            window_seconds=Decimal("1"),
        ),
        decimal_clock,
        decimal_sleep,
    )

    http_client = WebullHttpClient(
        HTTP_ENDPOINT,
        Decimal("10"),
        configuration.retry_policy,
        UrllibHttpBackend(),
        authentication,
        limiter,
        decimal_sleep,
        logger,
    )

    transport = WebullBrokerTransport(
        configuration,
        http_client,
        authentication,
        logger,
        utc_now,
    )

    adapter = WebullAdapter(transport)

    print("\nConnecting...")
    adapter.connect()

    try:
        print("\nAccount")
        print(adapter.get_account())

        print("\nCash")
        print(adapter.get_cash())

        print("\nPositions")
        print(adapter.get_positions())

        print("\nOpen Orders")
        print("-----------")
        orders = adapter.get_orders()
        print(orders)

        print("\nFills")
        print("-----")
        fills = adapter.get_fills()
        print(fills)

        run_optional_order_test(adapter)

    finally:
        print("\nDisconnecting...")
        adapter.disconnect()

    print("\nSmoke test completed.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
    except Exception as exc:
        print(
            f"\nSmoke test failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise