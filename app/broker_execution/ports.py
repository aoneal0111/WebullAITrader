from __future__ import annotations
from typing import Protocol,runtime_checkable,Any
from app.broker_execution.models import BrokerExecutionAuthorization
@runtime_checkable
class BrokerExecutionPort(Protocol):
    def execute(self,authorization:BrokerExecutionAuthorization)->Any: ...
