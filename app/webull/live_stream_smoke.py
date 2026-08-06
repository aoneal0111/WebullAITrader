"""Run a minimal Webull OpenAPI market-data streaming connectivity test.

Usage from the repository root::

    python -m app.webull.live_stream_smoke --symbol AAPL

The command never prints credentials. It connects through the official SDK,
subscribes to one US stock, prints the first decoded SDK payload, and exits.
"""

from __future__ import annotations

import argparse
from os import environ
from pprint import pformat
import sys
from urllib.parse import urlparse

from app.webull.sdk_streaming_adapter import (
    WebullMarketSubscription,
    WebullStreamingCredentials,
    create_official_stream_backend,
)


def _host_from_environment(name: str) -> str | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname or value


def _load_credentials() -> WebullStreamingCredentials:
    """Load canonical streaming names, with REST-style aliases for convenience."""

    values = dict(environ)
    values.setdefault("WEBULL_APP_KEY", values.get("WEBULL_API_KEY", ""))
    values.setdefault("WEBULL_APP_SECRET", values.get("WEBULL_API_SECRET", ""))

    for name in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET"):
        if values.get(name, "").strip() in {"YOUR_REAL_APP_KEY", "YOUR_REAL_APP_SECRET"}:
            raise ValueError(f"{name} still contains a placeholder value")

    return WebullStreamingCredentials.from_environment(values)


def _subscription_types() -> tuple[object, ...]:
    try:
        from webull.data.common.subscribe_type import SubscribeType
    except ImportError as exc:
        raise RuntimeError(
            "Webull OpenAPI SDK is unavailable; install webull-openapi-python-sdk"
        ) from exc
    return (SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name, SubscribeType.TICK.name)


def run(symbol: str, timeout_seconds: float) -> int:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must not be blank")

    credentials = _load_credentials()
    subscription = WebullMarketSubscription(
        category="US_STOCK",
        sub_types=_subscription_types(),
    )

    backend = create_official_stream_backend(
        credentials,
        subscription,
        receive_timeout_seconds=timeout_seconds,
        http_host=_host_from_environment("WEBULL_API_BASE_URL"),
        mqtt_host=_host_from_environment("WEBULL_DATA_API_HOST"),
    )

    print(
        f"Connecting to Webull stream: symbol={normalized_symbol} "
        f"region={credentials.region_id} client_id=<redacted>"
    )
    try:
        backend.connect()
        print("Connected. Subscribing to QUOTE, SNAPSHOT, and TICK...")
        backend.subscribe((normalized_symbol,))
        payload = backend.receive()
        if payload is None:
            print(f"No market-data payload arrived within {timeout_seconds:g} seconds.")
            return 2
        print("First Webull payload received:")
        print(pformat(payload, sort_dicts=False))
        return 0
    finally:
        try:
            backend.disconnect()
        except Exception as exc:  # best-effort cleanup after a failed connection
            print(f"Warning: stream cleanup failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL", help="US stock symbol (default: AAPL)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds to wait for the first payload (default: 30)",
    )
    args = parser.parse_args(argv)
    if args.timeout < 0:
        parser.error("--timeout must be non-negative")

    try:
        return run(args.symbol, args.timeout)
    except (RuntimeError, TypeError, ValueError, TimeoutError) as exc:
        print(f"Webull streaming smoke test failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Webull streaming smoke test failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
