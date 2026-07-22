from datetime import UTC,datetime,timedelta
from decimal import Decimal
from app.broker_execution import ExecutionMode,ExecutionSafetyGate
from app.execution_journal import JournalIntegrityStatus
from app.live_broker import *
from tests.broker_execution.helpers import request as safety_request,policy as safety_policy
STAMP=datetime(2026,7,21,20,4,tzinfo=UTC)
def authorization():return ExecutionSafetyGate().authorize(safety_request(mode=ExecutionMode.LIVE,policy=safety_policy(live_mode_enabled=True,require_human_authorization=False)))
def policy(**x):
 v={"live_execution_enabled":True,"maximum_account_snapshot_age_seconds":60,"maximum_order_quantity":100,"maximum_order_notional":Decimal("20000"),"maximum_daily_loss":Decimal("1000"),"allowed_symbols":("AAPL",)};v.update(x);return LiveExecutionPolicy(**v)
def capability(**x):
 v={"capability_id":"cap-1","enabled":True,"environment":"production-live","issued_at":STAMP-timedelta(minutes=1),"expires_at":STAMP+timedelta(minutes=5),"authorized_symbols":("AAPL",),"maximum_order_quantity":100,"maximum_order_notional":Decimal("20000")};v.update(x);return RuntimeLiveCapability(**v)
def confirmation(a,**x):
 v={"confirmation_id":"confirm-1","authorization_id":a.authorization_id,"proposal_id":a.proposal_id,"confirmed":True,"timestamp":STAMP-timedelta(minutes=1),"expires_at":STAMP+timedelta(minutes=5),"environment":"production-live"};v.update(x);return LiveHumanConfirmation(**v)
def evidence(a,**x):
 v={"authorization_id":a.authorization_id,"journal_record_id":"record-1","journal_record_hash":"hash-1","journal_sequence_number":1,"journal_integrity_status":JournalIntegrityStatus.VALID,"execution_ids_for_authorization":(),"timestamp":STAMP};v.update(x);return JournalAuthorizationEvidence(**v)
def snapshot(**x):
 v={"timestamp":STAMP,"environment":"production-live","available_buying_power":Decimal("50000"),"current_daily_realized_pnl":Decimal("0"),"symbol_positions":{},"open_authorization_ids":()};v.update(x);return LiveBrokerAccountSnapshot(**v)
def request(**x):
 a=x.pop("authorization",authorization());v={"authorization":a,"timestamp":STAMP,"policy":policy(),"runtime_capability":capability(),"human_confirmation":confirmation(a),"journal_evidence":evidence(a),"account_snapshot":snapshot(),"environment":"production-live","request_fingerprint":"live-fp-1"};v.update(x);return LiveExecutionRequest(**v)
