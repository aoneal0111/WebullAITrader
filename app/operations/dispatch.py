from dataclasses import dataclass
from app.operations.limits import OperationalState,validate_operational_limits
@dataclass(frozen=True,slots=True)
class LiveDispatchControls:
 configuration:object;emergency_stop:object;operational_state:OperationalState;metrics:object;logger:object|None=None
 def validate(self,operation,request,now):
  self.emergency_stop.permit(operation)
  if operation in ("SUBMIT","REPLACE"):
   try:return validate_operational_limits(request,self.operational_state,self.configuration,now)
   except ValueError as exc:
    self.metrics.increment("authorization_rejection_total")
    if self.logger:self.logger.log("risk_limit_rejected","failed",operation=operation,error_type=type(exc).__name__)
    raise
  return None
