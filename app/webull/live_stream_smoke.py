"""Run a minimal Webull OpenAPI market-data streaming connectivity test.

Usage from the repository root::

    python -m app.webull.live_stream_smoke --symbol AAPL

The command never prints credentials. It connects through the official SDK,
subscribes to one US stock, prints sanitized decoder metadata, remains connected
for the requested duration, and exits deliberately. It never submits orders.
"""

from __future__ import annotations

import argparse
from os import environ
import sys
from time import monotonic
from urllib.parse import urlparse

from app.webull.sdk_streaming_adapter import (
    WebullMarketSubscription,
    WebullStreamingCredentials,
    create_official_stream_backend,
)
from app.webull.errors import SerializationError
from app.webull.market_event_parser import (
    WebullMarketEventParser,
    decoder_failure_metadata,
    payload_metadata,
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


def run(
    symbol: str,
    timeout_seconds: float,
    *,
    summary_only: bool = False,
    require_zero_errors: bool = False,
) -> int:
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
        receive_timeout_seconds=min(1.0, timeout_seconds),
        http_host=_host_from_environment("WEBULL_API_BASE_URL"),
        mqtt_host=(
            _host_from_environment("WEBULL_STREAM_URL")
            or _host_from_environment("WEBULL_DATA_API_HOST")
        ),
    )

    print(
        f"Connecting to Webull stream: symbol={normalized_symbol} "
        f"region={credentials.region_id} client_id=<redacted>"
    )
    try:
        backend.connect()
        if not backend.registration_ready:
            raise RuntimeError("Webull streaming session registration is not ready")
        print("Connected. Subscribing to QUOTE, SNAPSHOT, and TICK...")
        backend.subscribe((normalized_symbol,))
        parser = WebullMarketEventParser()
        deadline = monotonic() + timeout_seconds
        decoded = 0
        decode_errors = 0
        decoded_by_class = {"QUOTE": 0, "SNAPSHOT": 0, "TRADE": 0}
        while monotonic() < deadline:
            payload = backend.receive()
            if payload is None:
                continue
            metadata = payload_metadata(payload)
            try:
                event = parser(payload)
            except Exception as exc:
                decode_errors += 1
                failure = decoder_failure_metadata(
                    payload,
                    exc if isinstance(exc, SerializationError) else SerializationError(
                        "unexpected parser failure"
                    ),
                )
                if not summary_only:
                    print(
                        "Payload classification: "
                        f"topic={failure['topic']} "
                        f"sdk_object_type={failure['sdk_object_type']} "
                        f"result_type={failure['protobuf_result_type']} "
                        f"class={failure['message_classification']} "
                        f"symbol={failure['symbol']} "
                        f"field={failure['failure_field']} "
                        f"decoder={failure['decoder_selected']} "
                        f"timestamp_type={failure['timestamp_field_type']} "
                        f"price_type={failure['price_field_type']} "
                        f"volume_type={failure['volume_field_type']} "
                        f"payload_length={failure['payload_length']} "
                        f"payload_hash={failure['payload_hash']} "
                        f"error_stage={failure['error_stage']} "
                        f"error_type={type(exc).__name__}"
                    )
                continue
            if event is None:
                if not summary_only:
                    print(
                        "Payload skipped: "
                        f"topic={metadata['topic']} "
                        f"class={metadata['message_classification']}"
                    )
                continue
            decoded += 1
            classification = str(metadata["message_classification"])
            if classification in decoded_by_class:
                decoded_by_class[classification] += 1
            if not summary_only:
                print(
                    "Payload decoded: "
                    f"topic={metadata['topic']} "
                    f"class={classification} "
                    f"payload_type={metadata['payload_type']} "
                    f"payload_length={metadata['payload_length']} "
                    f"protobuf_message_type={type(payload[1]).__name__} "
                    f"symbol={event.symbol} sequence={event.sequence} "
                    f"timestamp={event.timestamp.isoformat()} "
                    f"price={getattr(event.payload, 'price', None) or getattr(event.payload, 'bid', None)}"
                )
        print(
            f"Connected for {timeout_seconds:g} seconds; "
            f"decoded_events={decoded} "
            f"quotes={decoded_by_class['QUOTE']} "
            f"snapshots={decoded_by_class['SNAPSHOT']} "
            f"ticks={decoded_by_class['TRADE']} "
            f"decode_errors={decode_errors}. Disconnecting deliberately."
        )
        if require_zero_errors and decode_errors:
            return 3
        return 0 if decoded else 2
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
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="print aggregate decoder counts instead of every payload",
    )
    parser.add_argument(
        "--require-zero-errors",
        action="store_true",
        help="return a failure status if any payload fails decoding",
    )
    args = parser.parse_args(argv)
    if args.timeout < 0:
        parser.error("--timeout must be non-negative")

    try:
        return run(
            args.symbol,
            args.timeout,
            summary_only=args.summary_only,
            require_zero_errors=args.require_zero_errors,
        )
    except (RuntimeError, TypeError, ValueError, TimeoutError) as exc:
        print(
            "Webull streaming smoke test failed: "
            f"error_type={type(exc).__name__} "
            f"rejection_code={getattr(exc, 'error_code', None)} "
            f"request_id={getattr(exc, 'request_id', None)}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            "Webull streaming smoke test failed: "
            f"error_type={type(exc).__name__} "
            f"rejection_code={getattr(exc, 'error_code', None)} "
            f"request_id={getattr(exc, 'request_id', None)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
