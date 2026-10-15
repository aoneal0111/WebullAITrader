"""Read-only SDK quote evidence; never opens a ledger or constructs protection."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from math import isfinite
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4

from app.webull.protection_quotes import ProtectionQuoteDecoder, ProtectionQuoteError, validate_symbols


def observe(backend, symbols, *, duration_seconds=10, maximum_messages=200,
            clock=lambda: datetime.now(timezone.utc), timer=monotonic):
    validate_symbols(symbols, maximum=5)
    if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
            or not isfinite(duration_seconds) or not 1 <= duration_seconds <= 30
            or type(maximum_messages) is not int or not 1 <= maximum_messages <= 500):
        raise ValueError("Read window must be 1..30 seconds and message limit 1..500")
    report = dict(scope="READ_ONLY_QUOTE_FIELDS_NOT_HALT_OR_PROTECTION_VALIDATION",
                  state="NO_VALID_QUOTES", requested_symbols=list(symbols), messages=0,
                  quotes=0, ignored=0, errors={}, samples=[],
                  missing_symbols=list(symbols),
                  stream_connection_attempts=0, subscription_attempts=0,
                  orders=0, entries_enabled=False, status_authorization="NONE",
                  live_feed_validated=False, size_units="PROVIDER_REPORTED_NOT_INDEPENDENTLY_VERIFIED")
    found = set()
    try:
        report["stream_connection_attempts"] = 1
        backend.connect()
        if not backend.registration_ready:
            report["state"] = "REGISTRATION_UNAVAILABLE"
            return report
        decoder = ProtectionQuoteDecoder(symbols, backend.generation_metrics["generation"], clock=clock)
        report["subscription_attempts"] = 1
        backend.subscribe(symbols)
        deadline = timer() + duration_seconds
        while timer() < deadline and report["messages"] < maximum_messages:
            if not backend.registration_ready:
                report["state"] = "STREAM_INTERRUPTED"
                break
            envelope = backend.receive()
            if envelope is None:
                continue
            report["messages"] += 1
            try:
                event = decoder.decode(envelope)
            except ProtectionQuoteError as exc:
                key = str(exc)
                report["errors"][key] = report["errors"].get(key, 0) + 1
                continue
            if event is None:
                report["ignored"] += 1
                continue
            report["quotes"] += 1
            if event.symbol not in found:
                found.add(event.symbol)
                report["samples"].append(dict(symbol=event.symbol, provider_at=event.timestamp.isoformat(),
                    received_at=event.received_timestamp.isoformat(), inspected_at=event.dequeued_timestamp.isoformat(),
                    provider_age_seconds=(event.dequeued_timestamp-event.timestamp).total_seconds(),
                    bid=str(event.payload.bid), ask=str(event.payload.ask),
                    bid_size=str(event.payload.bid_size), ask_size=str(event.payload.ask_size)))
            if len(found) == len(symbols):
                break
        if report["state"] != "STREAM_INTERRUPTED":
            report["state"] = ("QUOTE_FIELDS_OBSERVED" if len(found) == len(symbols)
                               else "PARTIAL_QUOTE_COVERAGE" if found else "NO_VALID_QUOTES")
    except Exception:
        report["state"] = "STREAM_OR_DECODE_UNAVAILABLE"
    finally:
        try:
            backend.disconnect()
        except Exception:
            report["state"] = "STREAM_CLEANUP_FAILED"
    report["missing_symbols"] = [s for s in symbols if s not in found]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--symbols", default="SPY,QQQ")
    parser.add_argument("--seconds", type=int, choices=range(1, 31), default=10)
    args = parser.parse_args(argv)
    symbols = tuple(args.symbols.split(","))
    try:
        validate_symbols(symbols, maximum=5)
    except ValueError as exc:
        parser.error(str(exc))
    sdk = version("webull-openapi-python-sdk")
    if not args.online:
        print(json.dumps(dict(scope="OFFLINE_QUOTE_CHECK_NO_NETWORK_OR_ORDERS", state="OFFLINE_NO_REQUESTS",
                              sdk_version=sdk, requested_symbols=list(symbols), network_requests=0,
                              orders=0, status_authorization="NONE", live_feed_validated=False), indent=2))
        return 0
    try:
        if sdk != "2.0.14":
            raise ValueError("SDK schema requires review")
        from dotenv import load_dotenv
        from app.configuration.loader import load_configuration
        from app.webull.client_factories import market_data_configuration
        from app.webull.sdk_streaming_adapter import (
            WebullStreamingCredentials, WebullMarketSubscription, create_official_stream_backend,
        )
        from app.webull.stream_endpoint import WebullStreamEndpoint
        load_dotenv(dotenv_path=Path.cwd()/".env", override=False)
        c = market_data_configuration(load_configuration())
        endpoint = WebullStreamEndpoint.parse(c.stream_url)
        if not endpoint.tls_enable:
            raise ValueError("TLS stream required")
        backend = create_official_stream_backend(
            WebullStreamingCredentials(c.api_key, c.api_secret, "atlas-quote-probe-"+uuid4().hex),
            WebullMarketSubscription(category="US_STOCK", sub_types=("QUOTE",), depth=1),
            receive_timeout_seconds=.25, http_host=urlparse(c.api_base_url).hostname,
            mqtt_host=endpoint.mqtt_host, mqtt_port=endpoint.mqtt_port, tls_enable=True,
            transport=endpoint.transport, websocket_path=endpoint.websocket_path)
        report = observe(backend, symbols, duration_seconds=args.seconds)
        report.update(sdk_version=sdk, environment=c.environment.value)
    except Exception:
        report = dict(scope="READ_ONLY_QUOTE_CHECK_NO_ORDERS", state="CONFIGURATION_OR_SDK_UNAVAILABLE",
                      sdk_version=sdk, orders=0, status_authorization="NONE", live_feed_validated=False)
    print(json.dumps(report, indent=2))
    return 0 if report["state"] == "QUOTE_FIELDS_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
