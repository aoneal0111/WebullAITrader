from decimal import Decimal
from dataclasses import replace
from app.broker_execution import ExecutionMode,ExecutionSafetyGate
from app.paper_broker import *
from tests.paper_broker.helpers import request,policy,authorization,STAMP
from tests.broker_execution.helpers import request as safety_request,policy as safety_policy,human
def test_fill_deterministic_decimal():
 r=request(policy=policy(fill_price_adjustment=Decimal(".25")));x=PaperBrokerAdapter().execute(r);assert x==PaperBrokerAdapter().execute(r);assert x.status is PaperBrokerExecutionStatus.FILLED;assert x.fill_price==x.entry_price+Decimal(".25");assert x.filled_notional==x.fill_price*x.quantity_filled
def test_acknowledge():
 x=PaperBrokerAdapter().execute(request(policy=policy(immediate_fill=False)));assert x.status is PaperBrokerExecutionStatus.ACKNOWLEDGED and x.quantity_filled==0
def test_duplicate():
 a=authorization();x=PaperBrokerAdapter().execute(request(authorization=a,state=PaperBrokerState(STAMP,(a.authorization_id,))));assert x.status is PaperBrokerExecutionStatus.DUPLICATE
def test_rejected_authorization():
 a=ExecutionSafetyGate().authorize(safety_request(policy=safety_policy(kill_switch_active=True)));assert PaperBrokerAdapter().execute(request(authorization=a)).rejection_reason is PaperBrokerRejectionReason.AUTHORIZATION_NOT_APPROVED
def test_limits_and_adjustment_floor():
 assert PaperBrokerAdapter().execute(request(policy=policy(maximum_fill_quantity=1))).rejection_reason is PaperBrokerRejectionReason.INVALID_QUANTITY
 assert PaperBrokerAdapter().execute(request(policy=policy(fill_price_adjustment=Decimal("-1000")))).rejection_reason is PaperBrokerRejectionReason.INVALID_ENTRY_PRICE
def test_live_unsupported():
 base=safety_request(mode=ExecutionMode.LIVE,policy=safety_policy(live_mode_enabled=True,require_human_authorization=False));a=ExecutionSafetyGate().authorize(base);assert PaperBrokerAdapter().execute(request(authorization=a)).rejection_reason is PaperBrokerRejectionReason.UNSUPPORTED_MODE
