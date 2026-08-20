"""Bounded read-only Webull observation into Warrior V1's paper sidecar.

This module constructs only the official market-data client.  It has no trading
client, broker order port, account identifier, or order mutation capability.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Mapping

from app.catalysts import (
    CatalystAggregator,
    SECEdgarCatalystProvider,
    SECEdgarPolicy,
    WebullCatalystProvider,
    log_sec_edgar_provider_state,
)
from app.configuration import load_configuration
from app.live_scanner.session import scanner_session
from app.momentum_scanner.models import AssetClass, CatalystType, ScannerObservation
from app.webull.client_factories import MarketDataClientFactory, market_data_configuration
from app.webull.sdk_market_data import (
    LazyOfficialDataClient, WebullScannerReferenceProvider,
    WebullScannerUniverseProvider, _catalyst_response_rows, _recent_row,
    _response_rows, _row_date,
)

from .forward_models import (
    FloatProvenance, ForwardCaptureConfiguration, PaperAccountContext,
    PointInTimeObservation,
)
from .forward_queue import ForwardCaptureWriter
from .forward_report import EASTERN, build_daily_report, persist_daily_report
from .forward_runtime import WarriorForwardCaptureService
from .forward_store import ForwardCaptureStore
from .models import MinuteBar


def capture_once(path: Path, *, limit: int = 10) -> dict[str, object]:
    if limit <= 0 or limit > 50:
        raise ValueError("limit must be 1..50")
    now = datetime.now(UTC)
    configuration = load_configuration()
    market_config = market_data_configuration(configuration)
    lazy = LazyOfficialDataClient(lambda: MarketDataClientFactory(market_config).create())
    universe = WebullScannerUniverseProvider(lazy, clock=lambda: now, page_size=50)
    instruments = universe.list_symbols(AssetClass.STOCK)[:limit]
    catalyst_providers = [WebullCatalystProvider(lazy)]
    if configuration.sec_edgar is None:
        log_sec_edgar_provider_state(enabled=False)
    if configuration.sec_edgar is not None:
        catalyst_providers.append(
            SECEdgarCatalystProvider(
                SECEdgarPolicy(
                    user_agent=configuration.sec_edgar.user_agent,
                    freshness_days=configuration.sec_edgar.freshness_days,
                    timeout_seconds=configuration.sec_edgar.timeout_seconds,
                )
            )
        )
    references = WebullScannerReferenceProvider(
        lazy, universe, clock=lambda: now,
        environment=market_config.environment.value,
        catalyst_aggregator=CatalystAggregator(
            catalyst_providers,
            clock=lambda: now,
        ),
    )
    store = ForwardCaptureStore(path)
    capture_config = ForwardCaptureConfiguration(storage_path=path)
    writer = ForwardCaptureWriter(
        store, capacity=capture_config.queue_capacity,
        batch_size=capture_config.batch_size,
        flush_interval_seconds=capture_config.flush_interval_seconds,
    )
    service = WarriorForwardCaptureService(
        store, writer, capture_config=capture_config,
    )
    paper = PaperAccountContext(
        equity=Decimal("25000"), buying_power=Decimal("25000"),
        allowed_symbols=frozenset(item.display_symbol for item in instruments),
    )
    errors: list[dict[str, str]] = []
    observed = 0
    client = lazy.get()
    market_data = getattr(client, "market_data")
    for instrument in instruments:
        symbol = instrument.display_symbol
        try:
            reference = references.get_reference_data_for_instrument(instrument)
            observed_at = datetime.now(UTC)
            quote_rows = _safe_rows(
                lambda: market_data.get_quotes(
                    instrument.api_symbol, instrument.category or "US_STOCK",
                )
            )
            snapshot_rows = _safe_rows(
                lambda: market_data.get_snapshot(
                    symbols=[instrument.api_symbol],
                    category=instrument.category or "US_STOCK",
                )
            )
            quote = _merge_rows(quote_rows, snapshot_rows)
            bid = _quote_price(quote, ("bid_price", "bidPrice", "bid", "bid1"))
            ask = _quote_price(quote, ("ask_price", "askPrice", "ask", "ask1"))
            price = _quote_price(quote, ("price", "close", "latest_price", "last"))
            price = price or instrument.price
            if price is None:
                raise ValueError("current price unavailable")
            rows = _response_rows(market_data.get_history_bar(
                instrument.api_symbol, instrument.category or "US_STOCK", "M1",
                count="120", real_time_required=False,
            ))
            minute_bars = tuple(
                sorted(
                    (_minute_bar(symbol, row) for row in rows),
                    key=lambda item: item.timestamp,
                )
            )
            event_date = _catalyst_event_date(client, symbol, reference.catalyst, now)
            halt_known = instrument.tradable_status is not None
            observation = ScannerObservation(
                symbol=symbol, timestamp=observed_at, price=price,
                previous_close=reference.previous_close,
                current_volume=reference.current_volume or Decimal("0"),
                average_30_day_volume=reference.average_30_day_volume,
                float_shares=reference.float_shares, bid=bid, ask=ask,
                catalyst=reference.catalyst,
                catalyst_headline=reference.catalyst_headline,
                tradable=reference.tradable, halted=instrument.halted,
                asset_class=AssetClass.STOCK,
                catalyst_status=reference.catalyst_status,
            )
            service.observe(PointInTimeObservation(
                observation=observation, session=scanner_session(observed_at).value,
                bars=minute_bars,
                float_provenance=(
                    FloatProvenance.MARKET_CAP_PRICE_PROXY
                    if reference.float_shares is not None
                    else FloatProvenance.UNKNOWN
                ),
                catalyst_event_date=event_date,
                catalyst_source=(
                    "WEBULL_EARNINGS" if reference.catalyst is CatalystType.EARNINGS
                    else "WEBULL_SEC_FILINGS" if reference.catalyst is CatalystType.SEC_FILING
                    else "WEBULL_EARNINGS_SEC"
                ),
                quote_observed_at=observed_at,
                quote_freshness_seconds=Decimal("0"),
                halt_state_known=halt_known,
                volume_known=reference.current_volume is not None,
                historical_bars_available=bool(minute_bars),
            ), account=paper)
            observed += 1
        except Exception as exc:
            # Never serialize provider exception messages: signed request details
            # and credential-bearing headers are not capture data.
            errors.append({"symbol": symbol, "error_type": type(exc).__name__})
    writer.close()
    report = build_daily_report(store, now.astimezone(EASTERN).date())
    report_inserted, _report_duplicate = persist_daily_report(store, report)
    metrics = writer.metrics()
    return {
        "captured_at": now.isoformat(), "storage_path": str(path),
        "requested_symbols": len(instruments), "observed_symbols": observed,
        "funnel": dict(report.funnel),
        "paper_trades": report.paper_trades,
        "missing_data_counts": dict(report.missing_data_counts),
        "records_written": metrics.records_written + report_inserted,
        "duplicate_records": metrics.duplicate_records,
        "dropped_records": metrics.dropped_records,
        "queue_depth": metrics.queue_depth,
        "average_write_latency_ms": str(metrics.average_write_latency_ms),
        "maximum_write_latency_ms": str(metrics.maximum_write_latency_ms),
        "gui_refresh_frequency_hz": str(metrics.gui_refresh_frequency_hz),
        "errors": errors,
        "live_order_capability": False,
    }


def _safe_rows(call) -> tuple[Mapping[str, object], ...]:
    try:
        return _response_rows(call())
    except Exception:
        return ()


def _merge_rows(*groups) -> Mapping[str, object]:
    merged: dict[str, object] = {}
    for group in groups:
        if group:
            merged.update(group[0])
    return merged


def _quote_price(row: Mapping[str, object], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, Mapping):
            value = next((value.get(name) for name in ("price", "value") if value.get(name) is not None), None)
        elif isinstance(value, (tuple, list)) and value:
            first = value[0]
            value = first.get("price") if isinstance(first, Mapping) else first
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if parsed.is_finite() and parsed > 0:
            return parsed
    return None


def _minute_bar(symbol: str, row: Mapping[str, object]) -> MinuteBar:
    return MinuteBar(
        symbol, datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00")),
        Decimal(str(row["open"])), Decimal(str(row["high"])),
        Decimal(str(row["low"])), Decimal(str(row["close"])),
        Decimal(str(row["volume"])),
    )


def _catalyst_event_date(client, symbol: str, kind: CatalystType, now: datetime) -> date | None:
    if kind not in {CatalystType.EARNINGS, CatalystType.SEC_FILING}:
        return None
    fundamentals = getattr(client, "fundamentals")
    if kind is CatalystType.EARNINGS:
        response = fundamentals.get_earnings_calendar(symbol, "US_STOCK")
        containers, days = ("data", "items"), 2
    else:
        response = fundamentals.get_sec_filings(symbol, "US_STOCK")
        containers, days = ("data", "items", "filings"), 3
    rows, _supported = _catalyst_response_rows(response, containers=containers)
    recent = _recent_row(rows, now, days=days)
    return None if recent is None else _row_date(recent)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path", type=Path,
        default=Path("data/warrior_momentum_v1_forward/forward_capture.sqlite3"),
    )
    parser.add_argument("--limit", type=int, default=10)
    arguments = parser.parse_args()
    print(json.dumps(capture_once(arguments.path, limit=arguments.limit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
