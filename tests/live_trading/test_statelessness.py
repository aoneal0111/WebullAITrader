from app.live_trading import LiveTradingRuntime,LiveTradingStatus
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request
def test_runtime_constructor_state_contains_only_dependencies():
    runtime=LiveTradingRuntime(ResearchExecutor(),BrokerExecutor())
    assert set(vars(runtime))=={"_research_executor","_broker_executor"}
def test_same_runtime_has_no_cross_call_state_and_is_deterministic():
    research=ResearchExecutor();broker=BrokerExecutor(errors={0:RuntimeError()});runtime=LiveTradingRuntime(research,broker)
    failed=runtime.run(request(2,enabled=True,fail_fast=True));broker.errors={};success=runtime.run(request(2,enabled=True,fail_fast=False));again=runtime.run(request(2,enabled=True,fail_fast=False))
    assert failed.status is LiveTradingStatus.FAILED and success.status is LiveTradingStatus.COMPLETED
    assert success==again and success is not again and success.orders is not again.orders and success.summary is not again.summary and success.criteria is not again.criteria
