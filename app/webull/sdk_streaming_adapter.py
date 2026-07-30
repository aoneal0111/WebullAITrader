"""Construction helpers for the official Webull market-data streaming SDK.

This module is the only production boundary that imports
``webull.data.data_streaming_client.DataStreamingClient``. Keeping that import
lazy allows tests, paper trading, and offline tooling to run without loading the
third-party transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from app.webull.websocket_client import OfficialSdkStreamBackend


SDKClientFactory = Callable[..., object]
SessionIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class WebullStreamingCredentials:
    app_key: str
    app_secret: str
    session_id: str
    region_id: str = "us"

    def __post_init__(self) -> None:
        for name in ("app_key", "app_secret", "session_id", "region_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str] | None = None,
        *,
        session_id_factory: SessionIdFactory | None = None,
    ) -> "WebullStreamingCredentials":
        """Load SDK credentials and create a connection identifier when omitted.

        Webull's streaming ``session_id`` identifies the MQTT client session; it
        is not an OAuth access token. Operators may set ``WEBULL_STREAM_SESSION_ID``
        for stable diagnostics, otherwise Atlas creates a unique identifier.
        """

        source = environ if values is None else values
        required = {
            "app_key": "WEBULL_APP_KEY",
            "app_secret": "WEBULL_APP_SECRET",
        }
        missing = [
            environment_name
            for environment_name in required.values()
            if not source.get(environment_name, "").strip()
        ]
        if missing:
            raise ValueError(
                f"missing Webull streaming environment variables: {', '.join(sorted(missing))}"
            )

        create_session_id = (
            (lambda: f"atlas-{uuid4().hex}")
            if session_id_factory is None
            else session_id_factory
        )
        session_id = source.get("WEBULL_STREAM_SESSION_ID", "").strip() or create_session_id().strip()
        if not session_id:
            raise ValueError("session_id_factory must return a non-blank identifier")

        return cls(
            app_key=source[required["app_key"]],
            app_secret=source[required["app_secret"]],
            session_id=session_id,
            region_id=source.get("WEBULL_REGION_ID", "us"),
        )


@dataclass(frozen=True, slots=True)
class WebullMarketSubscription:
    """SDK arguments shared by a set of runtime symbols."""

    category: str
    sub_types: tuple[object, ...]
    depth: int | None = None
    overnight_required: bool | None = None

    def __post_init__(self) -> None:
        if not self.category.strip():
            raise ValueError("category must not be blank")
        if not self.sub_types:
            raise ValueError("at least one subscription type is required")
        if self.depth is not None and self.depth <= 0:
            raise ValueError("depth must be positive")

    def sdk_arguments(self, symbols: Sequence[str]) -> dict[str, object]:
        normalized = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )
        if not normalized:
            raise ValueError("at least one symbol is required")
        arguments: dict[str, object] = {
            "symbols": normalized,
            "category": self.category,
            "sub_types": self.sub_types,
        }
        if self.depth is not None:
            arguments["depth"] = self.depth
        if self.overnight_required is not None:
            arguments["overnight_required"] = self.overnight_required
        return arguments


def _official_client_factory(**kwargs: object) -> object:
    try:
        from webull.data.data_streaming_client import DataStreamingClient
    except ImportError as exc:
        raise RuntimeError(
            "Webull OpenAPI SDK is unavailable; install webull-openapi-python-sdk"
        ) from exc
    return DataStreamingClient(**kwargs)


def create_official_stream_backend(
    credentials: WebullStreamingCredentials,
    subscription: WebullMarketSubscription,
    *,
    client_factory: SDKClientFactory | None = None,
    receive_timeout_seconds: float = 1.0,
    http_host: str | None = None,
    mqtt_host: str | None = None,
    mqtt_port: int = 1883,
    tls_enable: bool = True,
    transport: str = "tcp",
) -> OfficialSdkStreamBackend:
    """Construct the official SDK client behind the runtime stream protocol."""

    if mqtt_port <= 0:
        raise ValueError("mqtt_port must be positive")

    factory = _official_client_factory if client_factory is None else client_factory
    client_arguments: dict[str, object] = {
        "app_key": credentials.app_key,
        "app_secret": credentials.app_secret,
        "region_id": credentials.region_id,
        "session_id": credentials.session_id,
        "mqtt_port": mqtt_port,
        "tls_enable": tls_enable,
        "transport": transport,
    }
    if http_host is not None:
        client_arguments["http_host"] = http_host
    if mqtt_host is not None:
        client_arguments["mqtt_host"] = mqtt_host

    client = factory(**client_arguments)
    return OfficialSdkStreamBackend(
        client,
        subscription_mapper=subscription.sdk_arguments,
        receive_timeout_seconds=receive_timeout_seconds,
    )


def create_official_market_subscription() -> WebullMarketSubscription:
    """Build the supported US quote/trade subscription using official enums."""

    try:
        from webull.data.common.subscribe_type import SubscribeType
    except ImportError as exc:
        raise RuntimeError(
            "Webull OpenAPI SDK is unavailable; "
            "install webull-openapi-python-sdk"
        ) from exc
    return WebullMarketSubscription(
        category="US_STOCK",
        sub_types=(
            SubscribeType.QUOTE.name,
            SubscribeType.SNAPSHOT.name,
            SubscribeType.TICK.name,
        ),
    )


__all__ = [
    "WebullMarketSubscription",
    "WebullStreamingCredentials",
    "create_official_market_subscription",
    "create_official_stream_backend",
]
