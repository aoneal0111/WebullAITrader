from datetime import UTC,datetime,timedelta
from app.transport_runtime import *
STAMP=datetime(2026,7,21,20,7,tzinfo=UTC)
def request(**x):
 v={"operation":"gateway.authenticate","payload":{"environment":"production-live"},"timestamp":STAMP};v.update(x);return TransportRequest(**v)
class FakeExecutor:
 def __init__(self,response=None,fail=False):self.response=response;self.fail=fail;self.requests=[]
 def execute(self,r):
  if not isinstance(r,TransportRequest):raise TypeError("request required")
  self.requests.append(r)
  if self.fail:raise ValueError("controlled")
  return self.response or TransportResponse(success=True,result={"authenticated":True},error="",correlation_id=r.correlation_id,timestamp=r.timestamp+timedelta(seconds=2))
def policy(**x):
 v={"runtime_enabled":True};v.update(x);return TransportRuntimePolicy(**v)
