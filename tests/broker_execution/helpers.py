from datetime import UTC,datetime,timedelta
from decimal import Decimal
from app.broker_execution import *
from tests.execution.helpers import proposal
STAMP=datetime(2026,7,21,20,2,tzinfo=UTC)
def policy(**changes):
    values={"kill_switch_active":False,"require_human_authorization":False,"maximum_order_quantity":100,"maximum_order_notional":Decimal("20000"),"maximum_symbol_position":Decimal("100"),"maximum_daily_loss":Decimal("1000"),"allowed_symbols":("AAPL",)};values.update(changes);return ExecutionSafetyPolicy(**values)
def snapshot(**changes):
    values={"timestamp":STAMP,"current_daily_realized_pnl":Decimal("0"),"symbol_positions":{}};values.update(changes);return BrokerAccountSnapshot(**values)
def request(**changes):
    p=changes.pop("proposal",proposal());values={"proposal":p,"mode":ExecutionMode.PAPER,"timestamp":STAMP,"policy":policy(),"account_snapshot":snapshot(),"human_authorization":None,"request_fingerprint":"fp-1"};values.update(changes);return BrokerExecutionRequest(**values)
def human(p,mode=ExecutionMode.LIVE,**changes):
    values={"authorization_id":"human-1","proposal_id":p.proposal_id,"approved":True,"timestamp":STAMP,"expires_at":STAMP+timedelta(minutes=5),"authorized_mode":mode};values.update(changes);return HumanAuthorization(**values)
