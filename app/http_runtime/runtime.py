from decimal import Decimal
from app.http_runtime.exceptions import HTTPExecutionError,HTTPValidationError
from app.http_runtime.models import HTTPExecutionRecord,HTTPRequest,HTTPResponse
from app.http_runtime.policies import HTTPRuntimePolicy
class HTTPRuntime:
 def __init__(self,executor,policy:HTTPRuntimePolicy):
  if not hasattr(executor,"execute"):raise HTTPValidationError("executor must implement execute")
  if not isinstance(policy,HTTPRuntimePolicy):raise HTTPValidationError("policy must be HTTPRuntimePolicy")
  if policy.redirects_enabled or policy.cookies_enabled or policy.compression_enabled:raise HTTPValidationError("optional HTTP behaviors are not implemented")
  self.executor=executor;self.policy=policy
 def execute(self,request):
  if not isinstance(request,HTTPRequest):raise HTTPValidationError("request must be HTTPRequest")
  if not self.policy.runtime_enabled:raise HTTPValidationError("HTTP runtime is disabled")
  try:response=self.executor.execute(request)
  except Exception as exc:raise HTTPExecutionError("HTTP executor failed") from exc
  if not isinstance(response,HTTPResponse):raise HTTPExecutionError("executor returned invalid response")
  if response.correlation_id!=request.correlation_id:raise HTTPExecutionError("response correlation mismatch")
  if response.timestamp<request.timestamp:raise HTTPExecutionError("response timestamp precedes request")
  duration=Decimal(str((response.timestamp-request.timestamp).total_seconds()));return HTTPExecutionRecord(request,response,request.timestamp,response.timestamp,duration,{"deterministic":True})
