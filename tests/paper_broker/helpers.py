from datetime import UTC,datetime
from decimal import Decimal
from app.broker_execution import ExecutionSafetyGate
from app.paper_broker import *
from tests.broker_execution.helpers import request as safety_request
STAMP=datetime(2026,7,21,20,3,tzinfo=UTC)
def authorization(**changes):return ExecutionSafetyGate().authorize(safety_request(**changes))
def policy(**changes):
    values={"maximum_fill_quantity":100};values.update(changes);return PaperBrokerPolicy(**values)
def request(**changes):
    values={"authorization":authorization(),"timestamp":STAMP,"policy":policy(),"state":PaperBrokerState(STAMP)};values.update(changes);return PaperBrokerExecutionRequest(**values)
