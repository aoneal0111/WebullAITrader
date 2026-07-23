from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from app.broker_execution import BrokerExecutionAuthorization,ExecutionMode
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.execution_journal import JournalIntegrityStatus
from app.live_broker.policies import LiveExecutionPolicy
from app.trade_proposals.models import TradeDirection,aware_timestamp
from app.trade_proposals.policies import decimal_value
class LiveExecutionDecision(StrEnum):READY="READY";BLOCKED="BLOCKED"
class LiveExecutionReason(StrEnum):
 READY="READY";INVALID_REQUEST="INVALID_REQUEST";AUTHORIZATION_NOT_APPROVED="AUTHORIZATION_NOT_APPROVED";AUTHORIZATION_NOT_LIVE="AUTHORIZATION_NOT_LIVE";LIVE_POLICY_DISABLED="LIVE_POLICY_DISABLED";CAPABILITY_REQUIRED="CAPABILITY_REQUIRED";CAPABILITY_INVALID="CAPABILITY_INVALID";CAPABILITY_EXPIRED="CAPABILITY_EXPIRED";HUMAN_CONFIRMATION_REQUIRED="HUMAN_CONFIRMATION_REQUIRED";HUMAN_CONFIRMATION_INVALID="HUMAN_CONFIRMATION_INVALID";JOURNAL_AUTHORIZATION_REQUIRED="JOURNAL_AUTHORIZATION_REQUIRED";JOURNAL_AUTHORIZATION_MISMATCH="JOURNAL_AUTHORIZATION_MISMATCH";JOURNAL_INTEGRITY_REQUIRED="JOURNAL_INTEGRITY_REQUIRED";JOURNAL_INTEGRITY_INVALID="JOURNAL_INTEGRITY_INVALID";EXECUTION_ALREADY_RECORDED="EXECUTION_ALREADY_RECORDED";ACCOUNT_SNAPSHOT_REQUIRED="ACCOUNT_SNAPSHOT_REQUIRED";ACCOUNT_SNAPSHOT_STALE="ACCOUNT_SNAPSHOT_STALE";ENVIRONMENT_MISMATCH="ENVIRONMENT_MISMATCH";SYMBOL_NOT_ALLOWED="SYMBOL_NOT_ALLOWED";QUANTITY_EXCEEDS_LIMIT="QUANTITY_EXCEEDS_LIMIT";NOTIONAL_EXCEEDS_LIMIT="NOTIONAL_EXCEEDS_LIMIT";DAILY_LOSS_LIMIT_REACHED="DAILY_LOSS_LIMIT_REACHED";INVALID_TIMESTAMP="INVALID_TIMESTAMP"
CHECK_NAMES=("request valid","authorization approved","authorization mode is LIVE","live policy enabled","request environment matches policy","runtime capability present","runtime capability enabled","runtime capability not expired","capability environment matches","capability authorizes symbol","human confirmation present","human confirmation valid","journal evidence present","journal authorization matches","journal integrity valid","authorization not previously executed","account snapshot present","account snapshot environment matches","account snapshot not stale","symbol allowed by policy","quantity within policy and capability limits","notional within policy and capability limits","daily loss limit not reached")
@dataclass(frozen=True,slots=True)
class LiveExecutionCheck:
 name:str;passed:bool;detail:str
 def __post_init__(self):
  if self.name not in CHECK_NAMES or not isinstance(self.passed,bool) or not isinstance(self.detail,str) or not self.detail:raise ValueError("invalid live execution check")
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail}
 @classmethod
 def from_dict(cls,v):return cls(v["name"],v["passed"],v["detail"])
def _ids_symbols(value,name):
 if not isinstance(value,(tuple,list)):raise ValueError(f"{name} must be sequence")
 result=tuple(x.strip().upper() if name=="authorized_symbols" and isinstance(x,str) else x.strip() if isinstance(x,str) else "" for x in value)
 if any(not x for x in result) or len(result)!=len(set(result)):raise ValueError(f"{name} must be unique nonempty values")
 return result
@dataclass(frozen=True,slots=True)
class RuntimeLiveCapability:
 capability_id:str;enabled:bool;environment:str;issued_at:datetime;expires_at:datetime;authorized_symbols:tuple[str,...];maximum_order_quantity:int;maximum_order_notional:Decimal;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.capability_id,str) or not self.capability_id.strip() or not isinstance(self.environment,str) or not self.environment.strip():raise ValueError("capability identifiers must be nonempty")
  if not isinstance(self.enabled,bool):raise ValueError("enabled must be boolean")
  object.__setattr__(self,"issued_at",aware_timestamp(self.issued_at));object.__setattr__(self,"expires_at",aware_timestamp(self.expires_at))
  if self.expires_at<self.issued_at:raise ValueError("expires_at cannot precede issued_at")
  object.__setattr__(self,"authorized_symbols",_ids_symbols(self.authorized_symbols,"authorized_symbols"))
  if isinstance(self.maximum_order_quantity,bool) or not isinstance(self.maximum_order_quantity,int) or self.maximum_order_quantity<=0:raise ValueError("maximum_order_quantity must be positive")
  n=decimal_value("maximum_order_notional",self.maximum_order_notional)
  if n<=0:raise ValueError("maximum_order_notional must be positive")
  object.__setattr__(self,"maximum_order_notional",n);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"capability_id":self.capability_id,"enabled":self.enabled,"environment":self.environment,"issued_at":self.issued_at.isoformat(),"expires_at":self.expires_at.isoformat(),"authorized_symbols":list(self.authorized_symbols),"maximum_order_quantity":self.maximum_order_quantity,"maximum_order_notional":str(self.maximum_order_notional),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["capability_id"],v["enabled"],v["environment"],datetime.fromisoformat(v["issued_at"]),datetime.fromisoformat(v["expires_at"]),tuple(v["authorized_symbols"]),v["maximum_order_quantity"],v["maximum_order_notional"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class LiveHumanConfirmation:
 confirmation_id:str;authorization_id:str;proposal_id:str;confirmed:bool;timestamp:datetime;expires_at:datetime;environment:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if any(not isinstance(getattr(self,n),str) or not getattr(self,n).strip() for n in ("confirmation_id","authorization_id","proposal_id","environment")):raise ValueError("confirmation identifiers must be nonempty")
  if not isinstance(self.confirmed,bool):raise ValueError("confirmed must be boolean")
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"expires_at",aware_timestamp(self.expires_at))
  if self.expires_at<self.timestamp:raise ValueError("expires_at cannot precede timestamp")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"confirmation_id":self.confirmation_id,"authorization_id":self.authorization_id,"proposal_id":self.proposal_id,"confirmed":self.confirmed,"timestamp":self.timestamp.isoformat(),"expires_at":self.expires_at.isoformat(),"environment":self.environment,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["confirmation_id"],v["authorization_id"],v["proposal_id"],v["confirmed"],datetime.fromisoformat(v["timestamp"]),datetime.fromisoformat(v["expires_at"]),v["environment"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class JournalAuthorizationEvidence:
 authorization_id:str;journal_record_id:str;journal_record_hash:str;journal_sequence_number:int;journal_integrity_status:JournalIntegrityStatus;execution_ids_for_authorization:tuple[str,...];timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if any(not isinstance(getattr(self,n),str) or not getattr(self,n).strip() for n in ("authorization_id","journal_record_id","journal_record_hash")):raise ValueError("journal identifiers must be nonempty")
  if isinstance(self.journal_sequence_number,bool) or not isinstance(self.journal_sequence_number,int) or self.journal_sequence_number<=0:raise ValueError("journal sequence must be positive")
  if not isinstance(self.journal_integrity_status,JournalIntegrityStatus):raise ValueError("journal integrity status invalid")
  object.__setattr__(self,"execution_ids_for_authorization",_ids_symbols(self.execution_ids_for_authorization,"execution_ids"));object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"authorization_id":self.authorization_id,"journal_record_id":self.journal_record_id,"journal_record_hash":self.journal_record_hash,"journal_sequence_number":self.journal_sequence_number,"journal_integrity_status":self.journal_integrity_status.value,"execution_ids_for_authorization":list(self.execution_ids_for_authorization),"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["authorization_id"],v["journal_record_id"],v["journal_record_hash"],v["journal_sequence_number"],JournalIntegrityStatus(v["journal_integrity_status"]),tuple(v["execution_ids_for_authorization"]),datetime.fromisoformat(v["timestamp"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class LiveBrokerAccountSnapshot:
 timestamp:datetime;environment:str;available_buying_power:Decimal;current_daily_realized_pnl:Decimal;symbol_positions:Mapping[str,Decimal];open_authorization_ids:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if not isinstance(self.environment,str) or not self.environment.strip():raise ValueError("environment must be nonempty")
  for n in ("available_buying_power","current_daily_realized_pnl"):object.__setattr__(self,n,decimal_value(n,getattr(self,n)))
  if not isinstance(self.symbol_positions,Mapping):raise ValueError("symbol_positions must be mapping")
  p={}
  for k,v in self.symbol_positions.items():
   s=k.strip().upper() if isinstance(k,str) else ""
   if not s or s in p:raise ValueError("position symbols invalid")
   p[s]=decimal_value("position",v)
  object.__setattr__(self,"symbol_positions",MappingProxyType(p));object.__setattr__(self,"open_authorization_ids",_ids_symbols(self.open_authorization_ids,"open_authorization_ids"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"timestamp":self.timestamp.isoformat(),"environment":self.environment,"available_buying_power":str(self.available_buying_power),"current_daily_realized_pnl":str(self.current_daily_realized_pnl),"symbol_positions":{k:str(v) for k,v in self.symbol_positions.items()},"open_authorization_ids":list(self.open_authorization_ids),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(datetime.fromisoformat(v["timestamp"]),v["environment"],v["available_buying_power"],v["current_daily_realized_pnl"],v["symbol_positions"],tuple(v.get("open_authorization_ids",())),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class LiveExecutionRequest:
 authorization:BrokerExecutionAuthorization;timestamp:datetime;policy:LiveExecutionPolicy;runtime_capability:RuntimeLiveCapability|None;human_confirmation:LiveHumanConfirmation|None;journal_evidence:JournalAuthorizationEvidence|None;account_snapshot:LiveBrokerAccountSnapshot|None;environment:str;request_fingerprint:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.authorization,BrokerExecutionAuthorization):raise ValueError("authorization must be BrokerExecutionAuthorization")
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if self.timestamp<self.authorization.timestamp:raise ValueError("request timestamp cannot precede authorization")
  if not isinstance(self.policy,LiveExecutionPolicy):raise ValueError("policy must be LiveExecutionPolicy")
  for n,t in (("runtime_capability",RuntimeLiveCapability),("human_confirmation",LiveHumanConfirmation),("journal_evidence",JournalAuthorizationEvidence),("account_snapshot",LiveBrokerAccountSnapshot)):
   if getattr(self,n) is not None and not isinstance(getattr(self,n),t):raise ValueError(f"{n} has invalid type")
  if not isinstance(self.environment,str) or not self.environment.strip() or not isinstance(self.request_fingerprint,str) or not self.request_fingerprint.strip():raise ValueError("environment and request_fingerprint must be nonempty")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"authorization":self.authorization.to_dict(),"timestamp":self.timestamp.isoformat(),"policy":self.policy.to_dict(),"runtime_capability":self.runtime_capability.to_dict() if self.runtime_capability else None,"human_confirmation":self.human_confirmation.to_dict() if self.human_confirmation else None,"journal_evidence":self.journal_evidence.to_dict() if self.journal_evidence else None,"account_snapshot":self.account_snapshot.to_dict() if self.account_snapshot else None,"environment":self.environment,"request_fingerprint":self.request_fingerprint,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(BrokerExecutionAuthorization.from_dict(v["authorization"]),datetime.fromisoformat(v["timestamp"]),LiveExecutionPolicy.from_dict(v["policy"]),RuntimeLiveCapability.from_dict(v["runtime_capability"]) if v.get("runtime_capability") else None,LiveHumanConfirmation.from_dict(v["human_confirmation"]) if v.get("human_confirmation") else None,JournalAuthorizationEvidence.from_dict(v["journal_evidence"]) if v.get("journal_evidence") else None,LiveBrokerAccountSnapshot.from_dict(v["account_snapshot"]) if v.get("account_snapshot") else None,v["environment"],v["request_fingerprint"],v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class LiveBrokerInvocation:
 invocation_id:str;authorization_id:str;proposal_id:str;request_fingerprint:str;symbol:str;direction:TradeDirection|None;quantity:Decimal;entry_price:Decimal;order_notional:Decimal;mode:ExecutionMode;environment:str;timestamp:datetime;decision:LiveExecutionDecision;reason:LiveExecutionReason;capability_id:str|None;human_confirmation_id:str|None;journal_record_id:str|None;account_snapshot_timestamp:datetime|None;policy_version:str;guard_version:str;checks:tuple[LiveExecutionCheck,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("invocation_id","authorization_id","proposal_id","request_fingerprint","symbol","environment","policy_version","guard_version"):
   if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
  for n in ("quantity","entry_price","order_notional"):object.__setattr__(self,n,decimal_value(n,getattr(self,n)))
  object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp))
  if self.account_snapshot_timestamp is not None:object.__setattr__(self,"account_snapshot_timestamp",aware_timestamp(self.account_snapshot_timestamp))
  if not isinstance(self.decision,LiveExecutionDecision) or not isinstance(self.reason,LiveExecutionReason) or not isinstance(self.mode,ExecutionMode):raise ValueError("invalid invocation enum")
  if (self.decision is LiveExecutionDecision.READY)!=(self.reason is LiveExecutionReason.READY):raise ValueError("decision and reason inconsistent")
  if not isinstance(self.checks,tuple) or tuple(x.name for x in self.checks)!=CHECK_NAMES:raise ValueError("checks must be stable ordered")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"invocation_id":self.invocation_id,"authorization_id":self.authorization_id,"proposal_id":self.proposal_id,"request_fingerprint":self.request_fingerprint,"symbol":self.symbol,"direction":self.direction.value if self.direction else None,"quantity":str(self.quantity),"entry_price":str(self.entry_price),"order_notional":str(self.order_notional),"mode":self.mode.value,"environment":self.environment,"timestamp":self.timestamp.isoformat(),"decision":self.decision.value,"reason":self.reason.value,"capability_id":self.capability_id,"human_confirmation_id":self.human_confirmation_id,"journal_record_id":self.journal_record_id,"account_snapshot_timestamp":self.account_snapshot_timestamp.isoformat() if self.account_snapshot_timestamp else None,"policy_version":self.policy_version,"guard_version":self.guard_version,"checks":[x.to_dict() for x in self.checks],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["direction"]=TradeDirection(d["direction"]) if d["direction"] else None;d["mode"]=ExecutionMode(d["mode"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);d["account_snapshot_timestamp"]=datetime.fromisoformat(d["account_snapshot_timestamp"]) if d["account_snapshot_timestamp"] else None;d["decision"]=LiveExecutionDecision(d["decision"]);d["reason"]=LiveExecutionReason(d["reason"]);d["checks"]=tuple(LiveExecutionCheck.from_dict(x) for x in d["checks"]);return cls(**d)
