from decimal import Decimal
from app.transport_runtime.exceptions import TransportExecutionError,TransportValidationError
from app.transport_runtime.models import TransportExecutionRecord,TransportRequest,TransportResponse
from app.transport_runtime.policies import TransportRuntimePolicy
class TransportRuntime:
 def __init__(self,executor,policy:TransportRuntimePolicy,telemetry_hook=None,rate_limit_hook=None):
  if not hasattr(executor,"execute"):raise TransportValidationError("executor must implement execute")
  if not isinstance(policy,TransportRuntimePolicy):raise TransportValidationError("policy must be TransportRuntimePolicy")
  if policy.retries_enabled:raise TransportValidationError("retries are not supported")
  if policy.telemetry_enabled and not hasattr(telemetry_hook,"record"):raise TransportValidationError("telemetry hook required")
  if policy.rate_limit_enabled and not hasattr(rate_limit_hook,"allow"):raise TransportValidationError("rate limit hook required")
  self.executor=executor;self.policy=policy;self.telemetry_hook=telemetry_hook;self.rate_limit_hook=rate_limit_hook
 def execute(self,request):
  if not isinstance(request,TransportRequest):raise TransportValidationError("request must be TransportRequest")
  if not self.policy.runtime_enabled:raise TransportValidationError("transport runtime is disabled")
  if self.policy.rate_limit_enabled and self.rate_limit_hook.allow(request) is not True:raise TransportExecutionError("transport operation rate limited")
  try:response=self.executor.execute(request)
  except Exception as exc:raise TransportExecutionError("transport executor failed") from exc
  if not isinstance(response,TransportResponse):raise TransportExecutionError("executor returned invalid response")
  if response.correlation_id!=request.correlation_id:raise TransportExecutionError("response correlation mismatch")
  if response.timestamp<request.timestamp:raise TransportExecutionError("response timestamp precedes request")
  duration=Decimal(str((response.timestamp-request.timestamp).total_seconds()));record=TransportExecutionRecord(request,response,request.timestamp,response.timestamp,duration,{"deterministic":True,"policy_version":self.policy.version})
  if self.policy.telemetry_enabled:self.telemetry_hook.record(record)
  return record
