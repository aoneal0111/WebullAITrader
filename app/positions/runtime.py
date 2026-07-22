from app.positions.exceptions import PositionsDependencyError
from app.positions.models import PositionsCriteriaResult,PositionsDecision,PositionsResult,PositionModel
from app.positions.validation import validate_dependencies,validate_request
from app.session import SessionSnapshot,SessionStatus


class DeterministicPositionsRuntime:
    def __init__(self,session_manager,broker_gateway,policy):
        validate_dependencies(session_manager,broker_gateway,policy);self._session_manager=session_manager;self._broker_gateway=broker_gateway;self._policy=policy
    def get_positions(self,request):
        request=validate_request(request)
        if not self._policy.enabled:return self._result(request,PositionsDecision.DISABLED,(),(False,False,False))
        try:snapshot=self._session_manager.state()
        except Exception as exc:raise PositionsDependencyError("session manager failed to resolve session") from exc
        if not isinstance(snapshot,SessionSnapshot):raise PositionsDependencyError("session manager returned invalid snapshot")
        valid=snapshot.status is SessionStatus.ACTIVE and snapshot.session is not None and snapshot.session.identifier.value==request.session_id
        if not valid:return self._result(request,PositionsDecision.SESSION_INVALID,(),(True,False,False))
        try:positions=self._broker_gateway.get_positions(request)
        except Exception:return self._result(request,PositionsDecision.GATEWAY_FAILURE,(),(True,True,False))
        if not isinstance(positions,tuple) or any(not isinstance(x,PositionModel) for x in positions):raise PositionsDependencyError("broker position gateway returned invalid positions")
        return self._result(request,PositionsDecision.SUCCESS,positions,(True,True,True))
    def _result(self,request,decision,positions,passed):
        names=("policy_enabled","session_active","gateway_succeeded");details=("positions policy enabled","matching active session resolved","broker position gateway returned broker-neutral positions")
        criteria=tuple(PositionsCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details))
        return PositionsResult(request.request_id,request.session_id,decision,positions,criteria,{"deterministic":True,"policy_version":self._policy.version})
