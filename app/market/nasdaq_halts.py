"""Read-only acquisition of the official Nasdaq Trader trade-halt feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from threading import Lock
from time import monotonic
from typing import Callable, Protocol
from xml.etree import ElementTree as ET

import httpx


NASDAQ_TRADE_HALT_FEED_URL = (
    "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
)
_MAX_PAYLOAD_BYTES = 1_000_000


class NasdaqHaltFeedStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True, slots=True)
class NasdaqTradeHalt:
    symbol: str
    market: str
    halt_date: date
    halt_time: time
    reason_code: str
    issue_name: str | None = None
    resumption_date: date | None = None
    resumption_quote_time: time | None = None
    resumption_trade_time: time | None = None

    @property
    def resumed(self) -> bool:
        return self.resumption_trade_time is not None


@dataclass(frozen=True, slots=True)
class NasdaqHaltSnapshot:
    status: NasdaqHaltFeedStatus
    records: tuple[NasdaqTradeHalt, ...] = ()

    def for_symbol(self, symbol: str) -> NasdaqTradeHalt | None:
        normalized = str(symbol).strip().upper()
        matches = tuple(
            record for record in self.records if record.symbol == normalized
        )
        return max(
            matches,
            key=lambda record: (record.halt_date, record.halt_time),
            default=None,
        )

    def active_for_symbol(self, symbol: str) -> NasdaqTradeHalt | None:
        record = self.for_symbol(symbol)
        return record if record is not None and not record.resumed else None


class NasdaqHaltFeedTransport(Protocol):
    def fetch(self) -> bytes: ...


class NasdaqTraderRSSFeedTransport:
    """Fetch only Nasdaq Trader's fixed HTTPS trade-halt endpoint."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"Accept": "application/rss+xml, application/xml;q=0.9"},
        )

    def fetch(self) -> bytes:
        response = self._client.get(NASDAQ_TRADE_HALT_FEED_URL)
        if response.status_code != 200:
            raise RuntimeError(f"Nasdaq halt feed HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "xml" not in content_type and "rss" not in content_type:
            raise ValueError("Nasdaq halt feed content type is not XML")
        payload = response.content
        if not payload or len(payload) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Nasdaq halt feed payload size is invalid")
        return payload

    def close(self) -> None:
        self._client.close()


class NasdaqTradeHaltSource:
    """One-minute cached, fail-closed view of official halt records.

    This source has no execution authority.  A failed refresh never invents a
    halt or resumption and never re-labels a halt as a positive catalyst.
    """

    def __init__(
        self,
        transport: NasdaqHaltFeedTransport | None = None,
        *,
        refresh_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if refresh_seconds < 60.0:
            raise ValueError("Nasdaq requests must be spaced at least 60 seconds")
        self._transport = transport or NasdaqTraderRSSFeedTransport()
        self._refresh_seconds = float(refresh_seconds)
        self._clock = clock
        self._lock = Lock()
        self._snapshot: NasdaqHaltSnapshot | None = None
        self._next_refresh_at = 0.0

    def snapshot(self) -> NasdaqHaltSnapshot:
        now = self._clock()
        with self._lock:
            if self._snapshot is not None and now < self._next_refresh_at:
                return self._snapshot
            self._next_refresh_at = now + self._refresh_seconds
            try:
                records = parse_nasdaq_trade_halt_rss(self._transport.fetch())
            except (ET.ParseError, TypeError, ValueError):
                self._snapshot = NasdaqHaltSnapshot(
                    NasdaqHaltFeedStatus.MALFORMED
                )
            except Exception:
                self._snapshot = NasdaqHaltSnapshot(
                    NasdaqHaltFeedStatus.UNAVAILABLE
                )
            else:
                self._snapshot = NasdaqHaltSnapshot(
                    NasdaqHaltFeedStatus.AVAILABLE,
                    records,
                )
            return self._snapshot

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()


def parse_nasdaq_trade_halt_rss(payload: bytes) -> tuple[NasdaqTradeHalt, ...]:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("Nasdaq halt feed payload must be non-empty bytes")
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Nasdaq halt feed payload exceeds the size limit")
    lowered = payload[:1024].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("Nasdaq halt feed declarations are not permitted")
    root = ET.fromstring(payload)
    if _local_name(root.tag).lower() != "rss":
        raise ValueError("Nasdaq halt feed root must be RSS")

    records: list[NasdaqTradeHalt] = []
    for item in (element for element in root.iter() if _local_name(element.tag).lower() == "item"):
        values = {
            _local_name(child.tag).lower(): (child.text or "").strip()
            for child in item
        }
        symbol = values.get("issuesymbol", "").upper()
        market = values.get("market", "").upper()
        reason = values.get("reasoncode", "").upper()
        if not symbol or not market or not reason:
            raise ValueError("Nasdaq halt item is missing required identity fields")
        records.append(
            NasdaqTradeHalt(
                symbol=symbol,
                market=market,
                halt_date=_parse_date(values.get("haltdate", ""), required=True),
                halt_time=_parse_time(values.get("halttime", ""), required=True),
                reason_code=reason,
                issue_name=values.get("issuename") or None,
                resumption_date=_parse_date(values.get("resumptiondate", "")),
                resumption_quote_time=_parse_time(values.get("resumptionquotetime", "")),
                resumption_trade_time=_parse_time(values.get("resumptiontradetime", "")),
            )
        )
    return tuple(sorted(records, key=lambda record: (record.symbol, record.halt_date, record.halt_time)))


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _parse_date(value: str, *, required: bool = False) -> date | None:
    text = value.strip()
    if not text:
        if required:
            raise ValueError("required Nasdaq halt date is missing")
        return None
    month, day, year = (int(part) for part in text.split("/"))
    return date(year, month, day)


def _parse_time(value: str, *, required: bool = False) -> time | None:
    text = value.strip()
    if not text:
        if required:
            raise ValueError("required Nasdaq halt time is missing")
        return None
    pieces = text.split(":")
    if len(pieces) not in {2, 3}:
        raise ValueError("Nasdaq halt time is malformed")
    hour, minute = int(pieces[0]), int(pieces[1])
    second = int(pieces[2]) if len(pieces) == 3 else 0
    return time(hour, minute, second)


__all__ = [
    "NASDAQ_TRADE_HALT_FEED_URL",
    "NasdaqHaltFeedStatus",
    "NasdaqHaltSnapshot",
    "NasdaqTradeHalt",
    "NasdaqTradeHaltSource",
    "NasdaqTraderRSSFeedTransport",
    "parse_nasdaq_trade_halt_rss",
]
