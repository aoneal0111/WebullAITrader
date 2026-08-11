"""Capture the fixed read-only Webull dataset used for V1 validation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.configuration import load_configuration
from app.webull.client_factories import MarketDataClientFactory, market_data_configuration
from app.webull.sdk_market_data import _response_rows

from .models import MinuteBar
from .validation_dataset import SessionReference, write_dataset

SYMBOLS = (
    "AUUD", "JWEL", "STKH", "BWMN", "VREX", "HZO",
    "SCKT", "DKI", "STFS", "OFAL", "THH", "WAFU",
)
NEW_YORK = ZoneInfo("America/New_York")


def capture(directory: Path) -> dict[str, object]:
    configuration = market_data_configuration(load_configuration())
    client = MarketDataClientFactory(configuration).create()
    market_data = getattr(client, "market_data")
    bars: list[MinuteBar] = []
    references: list[SessionReference] = []
    for symbol in SYMBOLS:
        minute_rows = _response_rows(market_data.get_history_bar(
            symbol, "US_STOCK", "M1", count="1650", real_time_required=False,
        ))
        symbol_bars = tuple(sorted((_minute_bar(symbol, row) for row in minute_rows), key=lambda item: item.timestamp))
        bars.extend(symbol_bars)
        daily_rows = _response_rows(market_data.get_history_bar(
            symbol, "US_STOCK", "D", count="40", real_time_required=False,
        ))
        daily = tuple(sorted((_daily_row(row) for row in daily_rows), key=lambda item: item[0]))
        session_dates = sorted({bar.timestamp.astimezone(NEW_YORK).date() for bar in symbol_bars})
        for session_date in session_dates:
            prior = tuple(item for item in daily if item[0] < session_date)
            if not prior:
                continue
            previous_close = prior[-1][1]
            volumes = tuple(item[2] for item in prior[-30:] if item[2] > 0)
            if not volumes:
                continue
            references.append(SessionReference(
                symbol, session_date.isoformat(), previous_close,
                sum(volumes, Decimal("0")) / len(volumes),
            ))
    return write_dataset(
        directory, captured_at=datetime.now(UTC), bars=bars,
        references=references, symbols=SYMBOLS,
    )


def _minute_bar(symbol: str, row: dict[str, object]) -> MinuteBar:
    return MinuteBar(
        symbol=symbol, timestamp=datetime.fromisoformat(str(row["time"])),
        open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
    )


def _daily_row(row: dict[str, object]):
    timestamp = datetime.fromisoformat(str(row["time"])).astimezone(NEW_YORK)
    return timestamp.date(), Decimal(str(row["close"])), Decimal(str(row["volume"]))


def main() -> int:
    manifest = capture(Path("data/warrior_momentum_v1_validation"))
    print(
        f"dataset_id={manifest['dataset_id']} bars={manifest['bar_count']} "
        f"dates={manifest['date_start']}..{manifest['date_end']} "
        f"symbols={manifest['symbol_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
