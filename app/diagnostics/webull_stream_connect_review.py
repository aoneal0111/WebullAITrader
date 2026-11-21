"""Explicit read-only market-data connection check; never composes trading."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import monotonic, perf_counter

from app.configuration import load_configuration, TradingEnvironment
from app.live_execution.broker_factory import build_webull_market_data_stream
from app.performance_diagnostics import performance_diagnostics
from app.webull.errors import safe_error_identity


def check(configuration, *, seconds=10, symbol='AAPL', factory=build_webull_market_data_stream):
    if configuration.environment is not TradingEnvironment.PAPER or configuration.live_trading_enabled:
        raise ValueError('Read-only check requires existing PAPER mode and live trading disabled')
    symbol = symbol.strip().upper()
    if not 1 <= seconds <= 30 or not symbol.isascii() or not symbol.isalnum() or len(symbol) > 10:
        raise ValueError('Bounded duration and one stock symbol required')
    report = dict(scope='READ_ONLY_WEBULL_STREAM_CONNECT_REVIEW_V1',
        checked_at=datetime.now(UTC).isoformat(), symbol=symbol, status='NOT_CONNECTED',
        stage='CONSTRUCT', normalized_events=0, orders=0, trading_runtime_started=False,
        provider_requests='MARKET_DATA_CONNECTION_AND_ONE_SYMBOL_SUBSCRIPTION_ONLY')
    transport = None
    try:
        # Explicitly avoid the desktop, broker account, Grok and order composition.
        transport = factory(configuration)
        if transport is None:
            report['status'] = 'STREAM_DISABLED_OR_CREDENTIALS_MISSING'
            return report
        report['stage'] = 'CONNECT'
        transport.connect()
        report['status'] = 'CONNECTED'
        report['stage'] = 'SUBSCRIBE'
        transport.subscribe((symbol,))
        report['status'] = 'SUBSCRIBED'
        report['stage'] = 'RECEIVE'
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            event = transport.read_event()
            if event is not None:
                report['normalized_events'] += 1
                report['last_event_type'] = event.event_type.value
                report['last_event_timestamp'] = event.timestamp.isoformat()
        report['status'] = 'PAYLOADS_RECEIVED' if report['normalized_events'] else 'SUBSCRIBED_NO_PAYLOADS'
    except Exception as error:
        report['status'] = 'FAILED'
        report['error'] = safe_error_identity(error)
        if error.__cause__ is not None:
            report['cause'] = safe_error_identity(error.__cause__)
    finally:
        if transport is not None:
            client = getattr(transport, 'client', None)
            backend = getattr(client, 'backend', None)
            report['connection'] = getattr(backend, 'connection_diagnostics', {})
            disconnect_started = perf_counter()
            try:
                stop = getattr(transport, 'request_stop', None)
                if callable(stop): stop()
                transport.disconnect(timeout_seconds=5)
                report['disconnect'] = 'COMPLETED'
            except Exception as error:
                report['disconnect'] = 'FAILED_OR_PENDING'
                report['disconnect_error'] = safe_error_identity(error)
            finally:
                report['disconnect_duration_ms'] = round((perf_counter() - disconnect_started) * 1000, 3)
                report['connection_after_disconnect'] = getattr(backend, 'connection_diagnostics', {})
        report['stream_observability'] = performance_diagnostics.stream_observability()
    return report


def main():
    import argparse
    from dotenv import dotenv_values
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--symbol', default='AAPL')
    args = parser.parse_args()
    configuration = load_configuration({**dotenv_values(args.repo/'.env'), **os.environ})
    report = check(configuration, seconds=args.seconds, symbol=args.symbol)
    # Exclusive output preserves previous captures. Never write settings/ledgers.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print('UPLOAD_FILE='+str(args.output), flush=True)
    print('STREAM_CHECK='+report['status'], flush=True)


if __name__ == '__main__':
    main()
