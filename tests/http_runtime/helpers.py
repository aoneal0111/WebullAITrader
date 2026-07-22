from datetime import UTC,datetime,timedelta
from app.http_runtime import *
STAMP=datetime(2026,7,21,20,8,tzinfo=UTC)
def request(**x):
 v={"method":HTTPMethod.POST,"url":"https://example.invalid/protocol-only","headers":{"content-type":"application/json"},"body":{"operation":"test"},"timestamp":STAMP};v.update(x);return HTTPRequest(**v)
class FakeHTTPExecutor:
 def __init__(self,response=None,fail=False):self.response=response;self.fail=fail;self.requests=[]
 def execute(self,r):
  if not isinstance(r,HTTPRequest):raise TypeError("HTTPRequest required")
  self.requests.append(r)
  if self.fail:raise ValueError("controlled")
  return self.response or HTTPResponse(status_code=200,headers={},body={"ok":True},correlation_id=r.correlation_id,timestamp=r.timestamp+timedelta(seconds=1))
def policy(**x):
 v={"runtime_enabled":True};v.update(x);return HTTPRuntimePolicy(**v)
