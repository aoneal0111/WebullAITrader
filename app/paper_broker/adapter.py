from __future__ import annotations
import hashlib,json
from decimal import Decimal
from app.broker_execution import BrokerExecutionAuthorization,BrokerExecutionPort,SafetyDecision
from app.committee.models import thaw_json_value
from app.paper_broker.models import *
from app.trade_proposals.models import TradeProposal

class PaperBrokerAdapter:
    name="paper_broker_adapter_v1"
    def execute(self,request:PaperBrokerExecutionRequest)->PaperBrokerExecutionResult:
        if isinstance(request,TradeProposal):return self._raw_proposal_rejection(request)
        if not isinstance(request,PaperBrokerExecutionRequest):raise ValueError("request must be a PaperBrokerExecutionRequest")
        a,p=request.authorization,request.policy
        integral=a.quantity.is_finite() and a.quantity==a.quantity.to_integral_value()
        quantity=int(a.quantity) if integral and a.quantity>=0 else 0
        reason=PaperBrokerRejectionReason.NONE;status=None
        if a.decision is not SafetyDecision.APPROVED:reason=PaperBrokerRejectionReason.AUTHORIZATION_NOT_APPROVED
        elif a.mode not in p.supported_modes:reason=PaperBrokerRejectionReason.UNSUPPORTED_MODE
        elif a.authorization_id in request.state.executed_authorization_ids:reason=PaperBrokerRejectionReason.DUPLICATE_AUTHORIZATION;status=PaperBrokerExecutionStatus.DUPLICATE
        elif not integral or quantity<=0:reason=PaperBrokerRejectionReason.INVALID_QUANTITY
        elif a.entry_price<=0:reason=PaperBrokerRejectionReason.INVALID_ENTRY_PRICE
        elif quantity>p.maximum_fill_quantity:reason=PaperBrokerRejectionReason.INVALID_QUANTITY
        adjusted=a.entry_price+p.fill_price_adjustment
        if reason is PaperBrokerRejectionReason.NONE and p.immediate_fill and adjusted<=0:reason=PaperBrokerRejectionReason.INVALID_ENTRY_PRICE
        if reason is not PaperBrokerRejectionReason.NONE:
            status=status or PaperBrokerExecutionStatus.REJECTED;filled=0;fill=notional=Decimal("0")
        elif p.immediate_fill:
            status=PaperBrokerExecutionStatus.FILLED;filled=quantity;fill=adjusted;notional=fill*filled
        else:
            status=PaperBrokerExecutionStatus.ACKNOWLEDGED;filled=0;fill=notional=Decimal("0")
        eid=_execution_id(a.authorization_id,a.request_fingerprint,a.proposal_id,request.timestamp,status,reason,filled,fill,p.version,self.name)
        metadata=dict(thaw_json_value(request.metadata));metadata.update({"deterministic":True,"authorization_decision":a.decision.value,"authorization_reason":a.reason.value,"supported_mode":a.mode in p.supported_modes,"immediate_fill":p.immediate_fill,"fill_price_adjustment":str(p.fill_price_adjustment),"policy_version":p.version,"adapter_version":self.name})
        return PaperBrokerExecutionResult(eid,a.authorization_id,a.proposal_id,a.request_fingerprint,a.symbol,a.direction,quantity,filled,a.entry_price,fill,notional,a.mode,request.timestamp,status,reason,p.version,self.name,metadata)
    def _raw_proposal_rejection(self,p):
        policy=PaperBrokerPolicy();reason=PaperBrokerRejectionReason.INVALID_AUTHORIZATION_TYPE;status=PaperBrokerExecutionStatus.REJECTED
        eid=_execution_id("INVALID",p.proposal_id,p.proposal_id,p.timestamp,status,reason,0,Decimal("0"),policy.version,self.name)
        metadata={"deterministic":True,"authorization_decision":"INVALID","authorization_reason":"INVALID_AUTHORIZATION_TYPE","supported_mode":False,"immediate_fill":policy.immediate_fill,"fill_price_adjustment":"0","policy_version":policy.version,"adapter_version":self.name}
        return PaperBrokerExecutionResult(eid,"INVALID",p.proposal_id,p.proposal_id,p.symbol,p.direction,0,0,Decimal("0"),Decimal("0"),Decimal("0"),__import__("app.broker_execution",fromlist=["ExecutionMode"]).ExecutionMode.PAPER,p.timestamp,status,reason,policy.version,self.name,metadata)

def _execution_id(a,f,p,t,s,r,q,price,policy,adapter):
    canonical=json.dumps({"authorization_id":a,"request_fingerprint":f,"proposal_id":p,"timestamp":t.isoformat(),"status":s.value,"rejection_reason":r.value,"quantity_filled":q,"fill_price":str(price),"policy_version":policy,"adapter_version":adapter},sort_keys=True,separators=(",",":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
