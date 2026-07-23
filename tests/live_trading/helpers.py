from datetime import timedelta
from app.live_trading import *
from tests.order_placement.fixtures import request as broker_request
from tests.order_placement.fixtures import enabled_policy
from tests.order_placement.helpers import FakeGateway,FakeSessionManager
from app.order_placement import DeterministicOrderPlacementRuntime
from tests.research_portfolio.helpers import request as research_request,runtime as research_runtime
def order(index):return LiveTradingOrderRequest(LiveTradingOrderIdentity(f"order-entry-{index}"),broker_request())
def request(count=2,enabled=False,fail_fast=True):
    research=research_request(1);at=research.requested_at
    return LiveTradingRequest(LiveTradingIdentity("live-1"),research,tuple(order(i) for i in range(count)),LiveTradingPolicy(enabled,fail_fast),at,at+timedelta(days=1))
def valid_broker_result(request):
    return DeterministicOrderPlacementRuntime(FakeSessionManager(),FakeGateway(),enabled_policy()).place_order(request)
class ResearchExecutor:
    def __init__(self,response=None,error=None,events=None):self.response=response;self.error=error;self.calls=[];self.events=events
    def run(self,request):
        self.calls.append(request)
        if self.events is not None:self.events.append("research")
        if self.error:raise self.error
        return self.response if self.response is not None else research_runtime()[0].run(request)
class BrokerExecutor:
    def __init__(self,responses=None,errors=None,callback=None,events=None):self.responses=list(responses or []);self.errors=errors or {};self.callback=callback;self.calls=[];self.events=events
    def place_order(self,request):
        index=len(self.calls);self.calls.append(request)
        if self.events is not None:self.events.append(f"broker-{index}")
        if index in self.errors:raise self.errors[index]
        if self.callback:return self.callback(request)
        if self.responses:return self.responses[index]
        return valid_broker_result(request)
