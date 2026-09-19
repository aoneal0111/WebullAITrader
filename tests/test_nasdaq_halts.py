from datetime import date, time

import pytest

from app.market.nasdaq_halts import (
    NasdaqHaltFeedStatus,
    NasdaqTradeHaltSource,
    parse_nasdaq_trade_halt_rss,
)


RSS = b'''<?xml version="1.0"?>
<rss xmlns:ndaq="http://www.nasdaqtrader.com/">
  <channel><item>
    <ndaq:HaltDate>09/18/2026</ndaq:HaltDate>
    <ndaq:HaltTime>14:31:05</ndaq:HaltTime>
    <ndaq:IssueSymbol>tjgc</ndaq:IssueSymbol>
    <ndaq:IssueName>TJGC Holdings</ndaq:IssueName>
    <ndaq:Market>NASDAQ</ndaq:Market>
    <ndaq:ReasonCode>T1</ndaq:ReasonCode>
    <ndaq:ResumptionDate>09/18/2026</ndaq:ResumptionDate>
    <ndaq:ResumptionQuoteTime>14:45:00</ndaq:ResumptionQuoteTime>
    <ndaq:ResumptionTradeTime>14:50:00</ndaq:ResumptionTradeTime>
  </item></channel>
</rss>'''


class Transport:
    def __init__(self, payload=RSS, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


def test_parse_official_namespaced_halt_and_resumption_fields():
    records = parse_nasdaq_trade_halt_rss(RSS)

    assert len(records) == 1
    record = records[0]
    assert record.symbol == "TJGC"
    assert record.market == "NASDAQ"
    assert record.reason_code == "T1"
    assert record.halt_date == date(2026, 9, 18)
    assert record.halt_time == time(14, 31, 5)
    assert record.resumption_trade_time == time(14, 50)
    assert record.resumed is True


def test_source_respects_official_one_minute_refresh_floor():
    with pytest.raises(ValueError, match="at least 60 seconds"):
        NasdaqTradeHaltSource(Transport(), refresh_seconds=59.9)


def test_source_caches_snapshot_and_supports_symbol_lookup():
    ticks = iter((100.0, 120.0, 159.9, 160.0))
    transport = Transport()
    source = NasdaqTradeHaltSource(transport, clock=lambda: next(ticks))

    first = source.snapshot()
    assert first.status is NasdaqHaltFeedStatus.AVAILABLE
    assert first.for_symbol("tjgc").reason_code == "T1"
    assert source.snapshot() is first
    assert source.snapshot() is first
    assert transport.calls == 1
    assert source.snapshot().status is NasdaqHaltFeedStatus.AVAILABLE
    assert transport.calls == 2


def test_failures_are_typed_and_never_invent_halt_records():
    unavailable = NasdaqTradeHaltSource(
        Transport(error=RuntimeError("offline")), clock=lambda: 1.0
    ).snapshot()
    malformed = NasdaqTradeHaltSource(
        Transport(payload=b"<not-rss/>"), clock=lambda: 1.0
    ).snapshot()

    assert unavailable.status is NasdaqHaltFeedStatus.UNAVAILABLE
    assert unavailable.records == ()
    assert malformed.status is NasdaqHaltFeedStatus.MALFORMED
    assert malformed.records == ()


def test_parser_rejects_dtd_and_incomplete_records():
    with pytest.raises(ValueError, match="declarations"):
        parse_nasdaq_trade_halt_rss(b'<!DOCTYPE rss><rss><channel/></rss>')
    with pytest.raises(ValueError, match="required identity"):
        parse_nasdaq_trade_halt_rss(b'<rss><channel><item/></channel></rss>')



def test_latest_symbol_record_controls_active_halt_state():
    payload = RSS.replace(
        b"</channel>",
        b"""<item>
          <ndaq:HaltDate>09/19/2026</ndaq:HaltDate>
          <ndaq:HaltTime>09:31:00</ndaq:HaltTime>
          <ndaq:IssueSymbol>TJGC</ndaq:IssueSymbol>
          <ndaq:Market>NASDAQ</ndaq:Market>
          <ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
        </item></channel>""",
    )
    snapshot = NasdaqTradeHaltSource(
        Transport(payload=payload), clock=lambda: 1.0
    ).snapshot()

    assert snapshot.active_for_symbol("tjgc").reason_code == "LUDP"


def test_resumed_latest_record_is_not_reported_as_active():
    snapshot = NasdaqTradeHaltSource(Transport(), clock=lambda: 1.0).snapshot()

    assert snapshot.for_symbol("TJGC") is not None
    assert snapshot.active_for_symbol("TJGC") is None
