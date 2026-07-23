from app.broker_execution import ExecutionMode,ExecutionSafetyGate
from app.execution_journal import JsonlExecutionJournal,JournalIntegrityStatus
from app.live_broker import *
from app.trade_proposals import TradeProposalEngine
from tests.broker_execution.helpers import request as safety_request,policy as safety_policy
from tests.live_broker.helpers import request,evidence
from tests.trade_proposals.helpers import request as proposal_request
def test_proposal_gate_journal_guard_pipeline(tmp_path):
 p=TradeProposalEngine().create(proposal_request());a=ExecutionSafetyGate().authorize(safety_request(proposal=p,mode=ExecutionMode.LIVE,policy=safety_policy(live_mode_enabled=True,require_human_authorization=False)));record=JsonlExecutionJournal(tmp_path/"journal").append_authorization(a);e=evidence(a,journal_record_id=record.record_id,journal_record_hash=record.record_hash,journal_sequence_number=record.sequence_number,journal_integrity_status=JournalIntegrityStatus.VALID);i=LiveExecutionGuard().authorize(request(authorization=a,journal_evidence=e));assert i.decision is LiveExecutionDecision.READY
