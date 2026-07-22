from datetime import timedelta
from decimal import Decimal
import pytest
from app.broker_execution import ExecutionMode,ExecutionSafetyGate
from app.execution_journal import JournalIntegrityStatus
from app.live_broker import *
from tests.broker_execution.helpers import request as safety_request,policy as safety_policy
from tests.live_broker.helpers import *
def test_ready_deterministic_and_checks():
 r=request();x=LiveExecutionGuard().authorize(r);assert x.decision is LiveExecutionDecision.READY and x==LiveExecutionGuard().authorize(r);assert len(x.checks)==23 and all(c.passed for c in x.checks)
@pytest.mark.parametrize("changes,reason",[
 ({"policy":policy(live_execution_enabled=False)},LiveExecutionReason.LIVE_POLICY_DISABLED),
 ({"runtime_capability":None},LiveExecutionReason.CAPABILITY_REQUIRED),
 ({"runtime_capability":capability(enabled=False)},LiveExecutionReason.CAPABILITY_INVALID),
 ({"runtime_capability":capability(expires_at=STAMP-timedelta(seconds=1))},LiveExecutionReason.CAPABILITY_EXPIRED),
 ({"environment":"staging"},LiveExecutionReason.ENVIRONMENT_MISMATCH),
 ({"human_confirmation":None},LiveExecutionReason.HUMAN_CONFIRMATION_REQUIRED),
 ({"journal_evidence":None},LiveExecutionReason.JOURNAL_AUTHORIZATION_REQUIRED),
 ({"journal_evidence":evidence(authorization(),authorization_id="wrong")},LiveExecutionReason.JOURNAL_AUTHORIZATION_MISMATCH),
 ({"journal_evidence":evidence(authorization(),journal_integrity_status=JournalIntegrityStatus.CORRUPTED)},LiveExecutionReason.JOURNAL_INTEGRITY_INVALID),
 ({"journal_evidence":evidence(authorization(),execution_ids_for_authorization=("done",))},LiveExecutionReason.EXECUTION_ALREADY_RECORDED),
 ({"account_snapshot":None},LiveExecutionReason.ACCOUNT_SNAPSHOT_REQUIRED),
 ({"account_snapshot":snapshot(timestamp=STAMP-timedelta(minutes=2))},LiveExecutionReason.ACCOUNT_SNAPSHOT_STALE),
 ({"policy":policy(allowed_symbols=("MSFT",))},LiveExecutionReason.SYMBOL_NOT_ALLOWED),
 ({"policy":policy(maximum_order_quantity=1)},LiveExecutionReason.QUANTITY_EXCEEDS_LIMIT),
 ({"policy":policy(maximum_order_notional=1)},LiveExecutionReason.NOTIONAL_EXCEEDS_LIMIT),
 ({"account_snapshot":snapshot(current_daily_realized_pnl=-1000)},LiveExecutionReason.DAILY_LOSS_LIMIT_REACHED),
 ({"policy":policy(maximum_daily_loss=0)},LiveExecutionReason.DAILY_LOSS_LIMIT_REACHED)])
def test_blockers(changes,reason):assert LiveExecutionGuard().authorize(request(**changes)).reason is reason
def test_paper_and_rejected_authorizations_block():
 paper=ExecutionSafetyGate().authorize(safety_request());assert LiveExecutionGuard().authorize(request(authorization=paper)).reason is LiveExecutionReason.AUTHORIZATION_NOT_LIVE
 rejected=ExecutionSafetyGate().authorize(safety_request(mode=ExecutionMode.LIVE,policy=safety_policy(kill_switch_active=True,live_mode_enabled=True,require_human_authorization=False)));assert LiveExecutionGuard().authorize(request(authorization=rejected)).reason is LiveExecutionReason.AUTHORIZATION_NOT_APPROVED
