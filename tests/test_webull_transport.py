from __future__ import annotations
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import json
import pytest

from app.live_execution.models import BrokerOrderRequest, LiveOrderType, LiveSide, TimeInForce
from app.market_data.models import HeartbeatPayload, MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.webull.configuration import *
from app.webull.errors import *
from app.webull.health import ConnectionHealth, update_health
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.transport import WebullBrokerTransport
from app.webull.websocket_client import WebullWebSocketClient
from app.live_execution.webull_adapter import WebullAdapter

D=Decimal; NOW=datetime(2026,7,18,tzinfo=UTC)
class Sink:
    def __init__(self): self.records=[]
    def emit(self,r): self.records.append(r)
class Clock:
    def __init__(self): self.value=D(0)
    def __call__(self): return self.value
def config(): return WebullConfiguration("https://api.sandbox.webull.com","account-secret",D("5"),RetryPolicy(3,D("1"),D("2"),D("4")),ReconnectPolicy(2,D("1")),WebSocketSettings("wss://data-api.sandbox.webull.com/mqtt"))

def test_configuration_validation():
    assert validate_configuration(config())==config()
    assert validate_configuration(
        replace_config(
            websocket=WebSocketSettings(
                "mqtts://data-api.sandbox.webull.com:1883"
            )
        )
    ).websocket.endpoint.startswith("mqtts://")
    with pytest.raises(ValueError): validate_configuration(replace_config(api_endpoint="http://unsafe"))
def replace_config(**changes):
    from dataclasses import replace
    return replace(config(),**changes)

def test_rate_limiter_queues_without_busy_wait():
    clock=Clock(); sleeps=[]
    def sleep(v): sleeps.append(v); clock.value+=v
    limiter=DeterministicRateLimiter(RateLimit(1,D("5")),clock,sleep); limiter.acquire(); limiter.acquire()
    assert sleeps==[D("5")]

def quote(seq): return MarketEvent(seq,NOW+timedelta(seconds=seq),"XYZ","webull",MarketEventType.QUOTE,QuotePayload(D("1"),D("2"),D("1"),D("1")))
class Stream:
    def __init__(self,items): self.items=list(items); self.connects=0; self.subscriptions=[]
    def connect(self): self.connects+=1
    def disconnect(self): pass
    def subscribe(self,c): self.subscriptions.append(c)
    def receive(self):
        value=self.items.pop(0)
        if isinstance(value,Exception): raise value
        return value

def test_websocket_reconnect_duplicates_sequence_and_heartbeat_health():
    stream=Stream((OSError(),quote(1),quote(1),MarketEvent(2,NOW+timedelta(seconds=2),None,"webull",MarketEventType.HEARTBEAT,HeartbeatPayload("c"))))
    sleeps=[]; sink=Sink(); client=WebullWebSocketClient(stream,lambda x:x,ReconnectPolicy(2,D("1")),lambda x:sleeps.append(x),StructuredLogger(sink))
    client.connect(); client.subscribe(("quotes","quotes")); assert client.receive().sequence==1
    assert client.health.reconnect_count==1 and client.receive() is None
    client.receive(); assert client.health.last_successful_heartbeat==NOW+timedelta(seconds=2)

def test_websocket_rejects_out_of_order_sequence():
    stream=Stream((quote(2),quote(1))); client=WebullWebSocketClient(stream,lambda x:x,ReconnectPolicy(1,D("1")),lambda x:None,StructuredLogger(Sink())); client.connect(); client.receive()
    with pytest.raises(SerializationError): client.receive()

def test_websocket_discards_delayed_same_symbol_quote_timestamp():
    delayed = MarketEvent(
        2, NOW, "XYZ", "webull", MarketEventType.QUOTE,
        QuotePayload(D("1"), D("2"), D("1"), D("1")),
    )
    sink = Sink()
    stream = Stream((quote(1), delayed))
    client = WebullWebSocketClient(
        stream, lambda x: x, ReconnectPolicy(1, D("1")), lambda x: None,
        StructuredLogger(sink),
    )
    client.connect()

    assert client.receive().sequence == 1
    assert client.receive() is None
    assert client.log.events == (quote(1),)
    assert any(
        record.get("reason") == "same_timeline_timestamp_regression"
        for record in sink.records
    )
    assert client.ordering_diagnostics["stale_by_event_type"] == (("QUOTE", 1),)
    assert client.ordering_diagnostics["stale_by_symbol"] == (("XYZ", 1),)


def test_websocket_accepts_interleaved_quote_trade_and_snapshot_timelines():
    events = (
        quote(1),
        MarketEvent(
            2, NOW, "XYZ", "webull", MarketEventType.TRADE,
            TradePayload(D("1.50"), D("10"), "tick-2"),
        ),
        MarketEvent(
            3, NOW - timedelta(seconds=1), "XYZ", "webull",
            MarketEventType.TRADE,
            TradePayload(D("1.49"), D("1000"), "snapshot"),
        ),
        MarketEvent(
            4, NOW - timedelta(seconds=2), "ABC", "webull",
            MarketEventType.QUOTE,
            QuotePayload(D("3"), D("3.01"), D("1"), D("1")),
        ),
    )
    client = WebullWebSocketClient(
        Stream(events), lambda x: x, ReconnectPolicy(1, D("1")),
        lambda x: None, StructuredLogger(Sink()),
    )
    client.connect()

    assert tuple(client.receive() for _ in events) == events
    assert client.ordering_diagnostics["stale_total"] == 0
    assert client.ordering_diagnostics["cross_timeline_regressions_accepted"] == 3

def test_health_monitoring_validation():
    health=update_health(ConnectionHealth(),connected=True,latency_microseconds=10,last_successful_heartbeat=NOW)
    assert health.connected and health.latency_microseconds==10
    with pytest.raises(ValueError): update_health(health,latency_microseconds=-1)

def test_logging_redacts_credentials():
    sink=Sink(); StructuredLogger(sink).log("auth","ok",password="p",access_token="t",account_id="a",symbol="XYZ")
    record=sink.records[0]; assert record["password"]==record["access_token"]==record["account_id"]=="[REDACTED]" and record["symbol"]=="XYZ"

class FakeHttp:
    def __init__(self): self.posts=[]
    def post(self,path,payload=None):
        self.posts.append((path,payload)); return {"order_id":"broker-1","client_order_id":payload["new_orders"][0]["client_order_id"],"status":"ACKNOWLEDGED","updated_timestamp":NOW.isoformat()}
    def get(self,path,query=None):
        if path.endswith("positions"): return [{"symbol":"XYZ","quantity":"2","average_price":"10","market_value":"20"}]
        if path.endswith("balance"): return {"settled_cash":"100","unsettled_cash":"5","currency":"USD"}
        if path.endswith("list"): return [{"account_id":"account-secret","account_type":"CASH","status":"ACTIVE"}]
        if path.endswith("open"): return []
        return []
def test_broker_protocol_transport_and_account_redaction():
    transport=WebullBrokerTransport(config(),FakeHttp(),None,StructuredLogger(Sink()),lambda:NOW); transport.connect()
    request=BrokerOrderRequest("r1","XYZ",LiveSide.BUY,LiveOrderType.LIMIT,D("2"),D("10"),None,TimeInForce.DAY)
    assert not hasattr(transport,"submit_order")
    with pytest.raises(PermissionError): transport.dispatch_submit(object(),request)
    adapter=WebullAdapter(transport); adapter._connected=True
    assert adapter.submit_order(request).broker_order_id=="broker-1"
    assert transport.get_positions()[0].quantity==D("2") and transport.get_cash().settled_cash==D("100")
    assert transport.get_account().account_id_redacted.endswith("cret") and "account-secret" not in transport.get_account().account_id_redacted

def test_import_boundary():
    package=Path(__file__).parents[1]/"app"/"webull"; content="\n".join(p.read_text(encoding="utf-8").lower() for p in package.glob("*.py"))
    forbidden=("app.ai","app.indicators","app.strategy","app.analytics","app.monte_carlo","app.stress_testing","app.risk","app.compliance")
    assert not any(f"from {x}" in content or f"import {x}" in content for x in forbidden)

