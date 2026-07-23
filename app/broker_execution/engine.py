from __future__ import annotations
import hashlib,json
from decimal import Decimal
from app.committee.models import thaw_json_value
from app.broker_execution.models import *
from app.trade_proposals.models import ProposalStatus,TradeDirection

class ExecutionSafetyGate:
    name="execution_safety_gate_v1"
    def authorize(self,request:BrokerExecutionRequest)->BrokerExecutionAuthorization:
        if not isinstance(request,BrokerExecutionRequest):raise ValueError("request must be a BrokerExecutionRequest")
        p,policy,s=request.proposal,request.policy,request.account_snapshot
        q=p.quantity; entry=p.proposed_entry_price; notional=entry*q
        current=s.symbol_positions.get(p.symbol,Decimal("0")); projected=current+(q if p.direction is TradeDirection.LONG else -q)
        h=request.human_authorization; human_required=policy.require_human_authorization
        human_present=h is not None
        human_valid=(not human_required) or (h is not None and h.approved and h.proposal_id==p.proposal_id and h.authorized_mode is request.mode and h.timestamp<=request.timestamp<=h.expires_at)
        symbol_ok=bool(p.symbol) and (not policy.allowed_symbols or p.symbol in policy.allowed_symbols)
        mode_ok=request.mode is ExecutionMode.PAPER or policy.live_mode_enabled
        loss_ok=policy.maximum_daily_loss>0 and s.current_daily_realized_pnl>-policy.maximum_daily_loss
        vals=(True,p.status is ProposalStatus.READY,symbol_ok,q>0,entry>0,not policy.kill_switch_active,mode_ok,human_valid,request.request_fingerprint not in s.recent_authorization_fingerprints,q<=policy.maximum_order_quantity,notional<=policy.maximum_order_notional,abs(projected)<=policy.maximum_symbol_position,loss_ok)
        reasons=(SafetyReason.INVALID_TIMESTAMP,SafetyReason.PROPOSAL_NOT_READY,SafetyReason.INVALID_SYMBOL,SafetyReason.INVALID_QUANTITY,SafetyReason.INVALID_ENTRY_PRICE,SafetyReason.KILL_SWITCH_ACTIVE,SafetyReason.LIVE_MODE_DISABLED,
          SafetyReason.HUMAN_AUTHORIZATION_REQUIRED if human_required and not human_present else SafetyReason.HUMAN_AUTHORIZATION_INVALID,SafetyReason.DUPLICATE_REQUEST,SafetyReason.QUANTITY_EXCEEDS_LIMIT,SafetyReason.NOTIONAL_EXCEEDS_LIMIT,SafetyReason.POSITION_EXCEEDS_LIMIT,SafetyReason.DAILY_LOSS_LIMIT_REACHED)
        failed=next((i for i,v in enumerate(vals) if not v),None); reason=SafetyReason.APPROVED if failed is None else reasons[failed]
        decision=SafetyDecision.APPROVED if failed is None else SafetyDecision.REJECTED
        checks=tuple(SafetyCheck(name,passed,"passed" if passed else reasons[i].value) for i,(name,passed) in enumerate(zip(CHECK_NAMES,vals,strict=True)))
        aid=_authorization_id(request,decision,reason,self.name)
        metadata=dict(thaw_json_value(request.metadata));metadata.update({"deterministic":True,"mode":request.mode.value,"order_notional":str(notional),"projected_symbol_position":str(projected),"kill_switch_active":policy.kill_switch_active,"live_mode_enabled":policy.live_mode_enabled,"human_authorization_required":human_required,"policy_version":policy.version,"engine_version":self.name})
        return BrokerExecutionAuthorization(aid,request.request_fingerprint,p.proposal_id,p.symbol,p.direction,q,entry,notional,projected,request.mode,request.timestamp,decision,reason,policy.version,self.name,h.authorization_id if h else None,checks,metadata)

def _authorization_id(r,d,reason,engine):
    canonical=json.dumps({"request_fingerprint":r.request_fingerprint,"proposal_id":r.proposal.proposal_id,"mode":r.mode.value,"timestamp":r.timestamp.isoformat(),"decision":d.value,"reason":reason.value,"policy_version":r.policy.version,"engine_version":engine},sort_keys=True,separators=(",",":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
