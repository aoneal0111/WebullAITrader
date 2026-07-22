from app.broker_execution import ExecutionSafetyGate
from app.execution_journal import JsonlExecutionJournal,paper_broker_state_from_recovery
from app.paper_broker import PaperBrokerAdapter,PaperBrokerExecutionStatus
from app.trade_proposals import TradeProposalEngine
from tests.broker_execution.helpers import request as safety_request
from tests.paper_broker.helpers import request as broker_request,STAMP
from tests.trade_proposals.helpers import request as proposal_request
def test_restart_reconstructs_duplicate_state(tmp_path):
 p=TradeProposalEngine().create(proposal_request());a=ExecutionSafetyGate().authorize(safety_request(proposal=p));path=tmp_path/"journal";JsonlExecutionJournal(path).append_authorization(a);result=PaperBrokerAdapter().execute(broker_request(authorization=a));JsonlExecutionJournal(path).append_execution(result)
 recovered=JsonlExecutionJournal(path).recover();state=paper_broker_state_from_recovery(recovered,STAMP);duplicate=PaperBrokerAdapter().execute(broker_request(authorization=a,state=state));assert duplicate.status is PaperBrokerExecutionStatus.DUPLICATE
