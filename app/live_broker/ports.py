from typing import Protocol
from app.live_broker.models import LiveBrokerInvocation
class LiveBrokerPort(Protocol):
 def execute(self,invocation:LiveBrokerInvocation):...
