"""Official Webull OpenAPI SDK adapter used by the live broker transport."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlparse

from webull.core.client import ApiClient
from webull.core.exception.exceptions import ClientException, ServerException
from webull.trade.trade_client import TradeClient

from app.webull.errors import (
    AuthenticationError,
    BrokerRejectionError,
    NetworkError,
    RateLimitError,
    SerializationError,
    UnknownBrokerError,
    ValidationError,
    WebullTransportError,
)


class WebullTradeClient(Protocol):
    """Subset of the official SDK client consumed by Atlas."""

    account_v2: Any
    order_v3: Any


OfficialTradeClientFactory = Callable[[ApiClient], WebullTradeClient]


def create_official_trade_client(
    *,
    app_key: str,
    app_secret: str,
    endpoint: str,
    timeout_seconds: Decimal,
    region_id: str = "us",
    trade_client_factory: OfficialTradeClientFactory = TradeClient,
) -> WebullTradeClient:
    """Create the official SDK client and run its token initialization."""

    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValidationError("Webull SDK endpoint must be a secure host")
    if not app_key.strip() or not app_secret.strip():
        raise AuthenticationError("Webull SDK credentials are required")
    if not isinstance(timeout_seconds, Decimal) or timeout_seconds <= 0:
        raise ValidationError("Webull SDK timeout must be positive")
    if not region_id.strip():
        raise ValidationError("Webull SDK region is required")

    api_client = ApiClient(
        app_key=app_key,
        app_secret=app_secret,
        region_id=region_id,
        port=parsed.port or 443,
        connect_timeout=float(timeout_seconds),
        timeout=float(timeout_seconds),
        auto_retry=True,
        max_retry_num=2,
    )
    api_client.add_endpoint(region_id, parsed.hostname)
    api_client.append_user_agent("Atlas", "1.0")

    # Mark SDK logging as explicitly configured so TradeClient does not create
    # its default rotating log file in the application working directory.
    api_client.set_stream_logger(log_level=logging.WARNING)

    try:
        # TradeClient invokes ClientInitializer, which creates/checks the
        # official access token and installs it on ApiClient.
        return trade_client_factory(api_client)
    except Exception as exc:
        raise _map_sdk_error(exc) from exc


class WebullHttpClient:
    """Path-compatible adapter whose operations are all official SDK calls."""

    def __init__(
        self,
        trade_client: WebullTradeClient,
        limiter,
        logger,
        *,
        request_guard=None,
        request_identity=None,
        endpoint: str = "",
    ) -> None:
        if trade_client is None:
            raise ValidationError("official Webull trade client is required")
        if not hasattr(limiter, "acquire"):
            raise ValidationError("Webull rate limiter is required")
        if not hasattr(logger, "log"):
            raise ValidationError("Webull logger is required")
        self._trade = trade_client
        self._limiter = limiter
        self._logger = logger
        self._request_guard = request_guard
        self._request_identity = request_identity
        self._endpoint = endpoint.rstrip("/")

    def get(self, path: str, *, query=None):
        query = dict(query or {})
        operations = {
            "/openapi/account/list": (
                lambda: self._trade.account_v2.get_account_list()
            ),
            "/openapi/assets/positions": (
                lambda: self._trade.account_v2.get_account_position(
                    _required(query, "account_id")
                )
            ),
            "/openapi/assets/balance": (
                lambda: self._trade.account_v2.get_account_balance(
                    _required(query, "account_id")
                )
            ),
            "/openapi/trade/order/open": (
                lambda: self._trade.order_v3.get_order_open(
                    _required(query, "account_id"),
                    page_size=query.get("page_size"),
                    last_client_order_id=query.get("last_client_order_id"),
                )
            ),
            "/openapi/trade/order/history": (
                lambda: self._trade.order_v3.get_order_history(
                    _required(query, "account_id"),
                    page_size=query.get("page_size"),
                    start_date=query.get("start_date"),
                    end_date=query.get("end_date"),
                    last_client_order_id=query.get("last_client_order_id"),
                )
            ),
        }
        return self._execute("GET", path, operations.get(path))

    def post(self, path: str, *, payload=None):
        body = _payload(payload)
        operations = {
            "/openapi/trade/order/place": (
                lambda: self._trade.order_v3.place_order(
                    _required(body, "account_id"),
                    _required(body, "new_orders"),
                    client_combo_order_id=body.get("client_combo_order_id"),
                )
            ),
            "/openapi/trade/order/cancel": (
                lambda: self._trade.order_v3.cancel_order(
                    _required(body, "account_id"),
                    _required(body, "client_order_id"),
                )
            ),
            "/openapi/trade/order/replace": (
                lambda: self._trade.order_v3.replace_order(
                    _required(body, "account_id"),
                    _required(body, "modify_orders"),
                    client_combo_order_id=body.get("client_combo_order_id"),
                )
            ),
        }
        return self._execute("POST", path, operations.get(path))

    def _execute(self, method: str, path: str, operation):
        if operation is None:
            raise ValidationError("unsupported Webull SDK operation")
        endpoint = f"{self._endpoint}{path}" if self._endpoint else path
        if self._request_guard is not None:
            self._request_guard.record(
                self._request_identity,
                endpoint=endpoint,
                capability_result="REQUESTED",
            )
        self._logger.log(
            "sdk_request",
            "started",
            method=method,
            path=path,
        )
        try:
            self._limiter.acquire()
            result = _decode_sdk_response(operation())
        except Exception as exc:
            if self._request_guard is not None:
                self._request_guard.record(
                    self._request_identity,
                    endpoint=endpoint,
                    capability_result="FAILED",
                )
            error = _map_sdk_error(exc)
            self._logger.log(
                "sdk_request",
                "failed",
                method=method,
                path=path,
                error_type=type(error).__name__,
            )
            if error is exc:
                raise
            raise error from exc
        if self._request_guard is not None:
            self._request_guard.record(
                self._request_identity,
                endpoint=endpoint,
                capability_result="SUCCEEDED",
            )
        self._logger.log(
            "sdk_request",
            "succeeded",
            method=method,
            path=path,
        )
        return result


def _decode_sdk_response(response):
    if isinstance(response, (dict, list)) or response is None:
        return response
    if not hasattr(response, "json"):
        raise SerializationError("Webull SDK returned an unsupported response")
    content = getattr(response, "content", None)
    if content in (b"", ""):
        return None
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise SerializationError(
            "Webull SDK returned malformed JSON"
        ) from exc


def _payload(value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Webull SDK request payload is required")
    return value


def _required(values: dict[str, Any], key: str):
    value = values.get(key)
    if value is None or value == "":
        raise ValidationError(f"{key} is required")
    return value


def _map_sdk_error(exc: Exception) -> WebullTransportError:
    if isinstance(exc, WebullTransportError):
        return exc
    if isinstance(exc, ServerException):
        status = exc.get_http_status()
        if status in (401, 403):
            return AuthenticationError("Webull authorization failed", status)
        if status == 429:
            return RateLimitError(
                "Webull rate limit exceeded",
                status,
                True,
            )
        if status in (408, 500, 502, 503, 504):
            return NetworkError(
                "transient Webull SDK failure",
                status,
                True,
            )
        if status in (400, 404, 405, 417, 422):
            return BrokerRejectionError(
                "Webull rejected the SDK request",
                status,
            )
        return UnknownBrokerError(
            "unexpected Webull SDK response",
            status,
        )
    if isinstance(exc, ClientException):
        code = str(exc.get_error_code()).upper()
        if "TOKEN" in code or "AUTH" in code:
            return AuthenticationError(
                "Webull SDK authentication failed"
            )
        return NetworkError(
            "Webull SDK client failure",
            retryable=True,
        )
    if isinstance(exc, (ValueError, TypeError)):
        return ValidationError("Webull SDK request validation failed")
    if isinstance(exc, (OSError, ConnectionError, TimeoutError)):
        return NetworkError(
            "Webull SDK network operation failed",
            retryable=True,
        )
    return UnknownBrokerError("unknown Webull SDK failure")


__all__ = [
    "OfficialTradeClientFactory",
    "WebullHttpClient",
    "WebullTradeClient",
    "create_official_trade_client",
]
