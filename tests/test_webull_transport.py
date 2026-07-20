from __future__ import annotations
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import json
import pytest

from app.live_execution.models import BrokerOrderRequest, LiveOrderType, LiveSide, TimeInForce
from app.market_data.models import HeartbeatPayload, MarketEvent, MarketEventType, QuotePayload
from app.webull.auth import AuthenticationManager, OAuthToken
from app.webull.configuration import *
from app.webull.errors import *
from app.webull.health import ConnectionHealth, update_health
from app.webull.http_client import HttpResponse, WebullHttpClient
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.transport import WebullBrokerTransport
from app.webull.websocket_client import WebullWebSocketClient
from app.live_execution.webull_adapter import WebullAdapter

D=Decimal; NOW=datetime(2026,7,18,tzinfo=UTC)
class Store:
    def __init__(self): self.value=None
    def load(self): return self.value
    def save(self,v): self.value=v
    def clear(self): self.value=None
class Tokens:
    def __init__(self): self.refreshes=0
    def exchange_code(self,c): return {"access_token":"secret-access","refresh_token":"secret-refresh","expires_in":"10","rt_expires_in":"100"}
    def refresh(self,t): self.refreshes+=1; return {"access_token":"new-access","refresh_token":"new-refresh","expires_in":"10","rt_expires_in":"100"}
    def verify(self,t): return True
class Sink:
    def __init__(self): self.records=[]
    def emit(self,r): self.records.append(r)
class Auth:
    def token(self): return OAuthToken("access", "refresh", NOW+timedelta(days=1), NOW+timedelta(days=2))
    def verify(self): return True
class Clock:
    def __init__(self): self.value=D(0)
    def __call__(self): return self.value
class Backend:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def send(self,*args): self.calls.append(args); value=self.responses.pop(0); 
    
def config(): return WebullConfiguration("https://api.sandbox.webull.com","account-secret",D("5"),RetryPolicy(3,D("1"),D("2"),D("4")),ReconnectPolicy(2,D("1")),WebSocketSettings("wss://data-api.sandbox.webull.com/mqtt"))

def test_authentication_lifecycle_and_refresh():
    now=[NOW]; store=Store(); endpoint=Tokens(); manager=AuthenticationManager(endpoint,store,lambda:now[0])
    token=manager.login("code"); assert token.access_token=="secret-access" and manager.verify()
    now[0]=NOW+timedelta(seconds=11); assert manager.token().access_token=="new-access" and endpoint.refreshes==1
    now[0]=NOW+timedelta(seconds=200)
    with pytest.raises(AuthenticationError): manager.token()

def test_configuration_validation():
    assert validate_configuration(config())==config()
    with pytest.raises(ValueError): validate_configuration(replace_config(api_endpoint="http://unsafe"))
def replace_config(**changes):
    from dataclasses import replace
    return replace(config(),**changes)

def make_http(responses):
    class B:
        def __init__(self): self.responses=list(responses); self.calls=[]
        def send(self,*args):
            self.calls.append(args); value=self.responses.pop(0)
            if isinstance(value,Exception): raise value
            return value
    backend=B(); clock=Clock(); sleeps=[]; limiter=DeterministicRateLimiter(RateLimit(100,D("60")),clock,lambda v:sleeps.append(v))
    sink=Sink(); client=WebullHttpClient(config().api_endpoint,D("5"),config().retry_policy,backend,Auth(),limiter,lambda v:sleeps.append(v),StructuredLogger(sink))
    return client,backend,sleeps,sink

def test_http_methods_and_deterministic_serialization():
    responses=[HttpResponse(200,(),b'{"ok":true}') for _ in range(4)]; client,backend,_,_=make_http(responses)
    assert client.get("/x",query={"b":"2","a":"1"})=={"ok":True}; client.post("/x",payload={"b":2,"a":1}); client.put("/x",payload={}); client.delete("/x")
    assert backend.calls[0][1].endswith("?a=1&b=2") and backend.calls[1][3]==b'{"a":1,"b":2}'

def test_http_retry_transient_only_and_retry_after():
    client,backend,sleeps,_=make_http((HttpResponse(503,(),b''),HttpResponse(200,(),b'{}')))
    assert client.get("/x")=={} and sleeps==[D("1")]
    client,_,sleeps,_=make_http((HttpResponse(429,(("Retry-After","3"),),b''),HttpResponse(200,(),b'{}')))
    client.get("/x"); assert sleeps==[D("3")]
    client,backend,sleeps,_=make_http((HttpResponse(401,(),b''),))
    with pytest.raises(AuthenticationError): client.get("/x")
    assert not sleeps

@pytest.mark.parametrize(("status","error"),((400,BrokerRejectionError),(401,AuthenticationError),(429,RateLimitError),(503,NetworkError),(520,UnknownBrokerError)))
def test_error_mapping(status,error):
    client,_,_,_=make_http(tuple(HttpResponse(status,(),b'') for _ in range(3)))
    with pytest.raises(error): client.get("/x")

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
    transport=WebullBrokerTransport(config(),FakeHttp(),Auth(),StructuredLogger(Sink()),lambda:NOW); transport.connect()
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

