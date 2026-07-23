from app.broker_execution import ExecutionSafetyGate
from app.paper_broker import PaperBrokerAdapter
from tests.broker_execution.helpers import request as safety_request
from tests.paper_broker.helpers import request as broker_request
def authorization():return ExecutionSafetyGate().authorize(safety_request())
def execution(a=None):
 a=a or authorization();return PaperBrokerAdapter().execute(broker_request(authorization=a))
