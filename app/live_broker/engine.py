from __future__ import annotations
import hashlib,json
from decimal import Decimal
from app.broker_execution import ExecutionMode,SafetyDecision
from app.committee.models import thaw_json_value
from app.execution_journal import JournalIntegrityStatus
from app.live_broker.models import *
class LiveExecutionGuard:
 name="live_execution_guard_v1"
 def authorize(self,r:LiveExecutionRequest)->LiveBrokerInvocation:
  if not isinstance(r,LiveExecutionRequest):raise ValueError("request must be LiveExecutionRequest")
  a,p,c,h,j,s=r.authorization,r.policy,r.runtime_capability,r.human_confirmation,r.journal_evidence,r.account_snapshot
  age=Decimal(str((r.timestamp-s.timestamp).total_seconds())) if s else None
  capability_present=c is not None or not p.require_runtime_capability
  human_present=h is not None or not p.require_human_confirmation
  journal_present=j is not None or not p.require_journal_authorization
  integrity_ok=(j is not None and j.journal_integrity_status is JournalIntegrityStatus.VALID) or not p.require_valid_journal_integrity
  q_ok=a.quantity<=p.maximum_order_quantity and (c is None or a.quantity<=c.maximum_order_quantity)
  n_ok=a.order_notional<=p.maximum_order_notional and (c is None or a.order_notional<=c.maximum_order_notional)
  vals=(True,a.decision is SafetyDecision.APPROVED,a.mode is ExecutionMode.LIVE,p.live_execution_enabled,r.environment==p.required_environment,capability_present,(c is not None and c.enabled) or not p.require_runtime_capability,(c is not None and r.timestamp<=c.expires_at) or not p.require_runtime_capability,(c is not None and c.environment==r.environment==p.required_environment) or not p.require_runtime_capability,(c is not None and a.symbol in c.authorized_symbols) or not p.require_runtime_capability,human_present,(h is not None and h.confirmed and h.authorization_id==a.authorization_id and h.proposal_id==a.proposal_id and h.environment==r.environment and h.timestamp<=r.timestamp<=h.expires_at) or not p.require_human_confirmation,journal_present,(j is not None and j.authorization_id==a.authorization_id) or not p.require_journal_authorization,integrity_ok,(j is None or not j.execution_ids_for_authorization) or not p.reject_previously_executed_authorizations,s is not None,(s is not None and s.environment==r.environment),(s is not None and age is not None and age>=0 and age<=p.maximum_account_snapshot_age_seconds),not p.allowed_symbols or a.symbol in p.allowed_symbols,q_ok,n_ok,s is not None and p.maximum_daily_loss>0 and s.current_daily_realized_pnl>-p.maximum_daily_loss)
  reasons=(LiveExecutionReason.INVALID_REQUEST,LiveExecutionReason.AUTHORIZATION_NOT_APPROVED,LiveExecutionReason.AUTHORIZATION_NOT_LIVE,LiveExecutionReason.LIVE_POLICY_DISABLED,LiveExecutionReason.ENVIRONMENT_MISMATCH,LiveExecutionReason.CAPABILITY_REQUIRED,LiveExecutionReason.CAPABILITY_INVALID,LiveExecutionReason.CAPABILITY_EXPIRED,LiveExecutionReason.ENVIRONMENT_MISMATCH,LiveExecutionReason.CAPABILITY_INVALID,LiveExecutionReason.HUMAN_CONFIRMATION_REQUIRED,LiveExecutionReason.HUMAN_CONFIRMATION_INVALID,LiveExecutionReason.JOURNAL_AUTHORIZATION_REQUIRED,LiveExecutionReason.JOURNAL_AUTHORIZATION_MISMATCH,LiveExecutionReason.JOURNAL_INTEGRITY_INVALID if j else LiveExecutionReason.JOURNAL_INTEGRITY_REQUIRED,LiveExecutionReason.EXECUTION_ALREADY_RECORDED,LiveExecutionReason.ACCOUNT_SNAPSHOT_REQUIRED,LiveExecutionReason.ENVIRONMENT_MISMATCH,LiveExecutionReason.ACCOUNT_SNAPSHOT_STALE,LiveExecutionReason.SYMBOL_NOT_ALLOWED,LiveExecutionReason.QUANTITY_EXCEEDS_LIMIT,LiveExecutionReason.NOTIONAL_EXCEEDS_LIMIT,LiveExecutionReason.DAILY_LOSS_LIMIT_REACHED)
  failed=next((i for i,x in enumerate(vals) if not x),None);reason=LiveExecutionReason.READY if failed is None else reasons[failed];decision=LiveExecutionDecision.READY if failed is None else LiveExecutionDecision.BLOCKED
  checks=tuple(LiveExecutionCheck(n,v,"passed" if v else reasons[i].value) for i,(n,v) in enumerate(zip(CHECK_NAMES,vals,strict=True)))
  iid=_id(a,r,decision,reason,c.capability_id if c else None,j.journal_record_id if j else None,self.name)
  metadata=dict(thaw_json_value(r.metadata));metadata.update({"deterministic":True,"live_execution_enabled":p.live_execution_enabled,"required_environment":p.required_environment,"capability_required":p.require_runtime_capability,"human_confirmation_required":p.require_human_confirmation,"journal_authorization_required":p.require_journal_authorization,"valid_journal_integrity_required":p.require_valid_journal_integrity,"duplicate_rejection_enabled":p.reject_previously_executed_authorizations,"order_notional":str(a.order_notional),"snapshot_age_seconds":str(age) if age is not None else None,"policy_version":p.version,"guard_version":self.name})
  return LiveBrokerInvocation(iid,a.authorization_id,a.proposal_id,r.request_fingerprint,a.symbol,a.direction,a.quantity,a.entry_price,a.order_notional,a.mode,r.environment,r.timestamp,decision,reason,c.capability_id if c else None,h.confirmation_id if h else None,j.journal_record_id if j else None,s.timestamp if s else None,p.version,self.name,checks,metadata)
def _id(a,r,d,reason,c,j,g):
 x=json.dumps({"authorization_id":a.authorization_id,"proposal_id":a.proposal_id,"request_fingerprint":r.request_fingerprint,"environment":r.environment,"timestamp":r.timestamp.isoformat(),"decision":d.value,"reason":reason.value,"capability_id":c,"journal_record_id":j,"policy_version":r.policy.version,"guard_version":g},sort_keys=True,separators=(",",":"));return hashlib.sha256(x.encode()).hexdigest()
