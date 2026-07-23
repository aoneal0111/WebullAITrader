from __future__ import annotations
import hashlib,json
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.models import aware_timestamp
def _canon(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False)
def _hash(kind,v):return hashlib.sha256(_canon({"kind":kind,**v}).encode()).hexdigest()
@dataclass(frozen=True,slots=True,kw_only=True)
class TransportRequest:
 request_id:str="";operation:str;payload:Mapping[str,JSONValue];correlation_id:str="";timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.operation,str) or not self.operation.strip():raise ValueError("operation must be nonempty")
  t=aware_timestamp(self.timestamp);p=freeze_json_mapping("payload",self.payload);m=freeze_json_mapping("metadata",self.metadata);base={"operation":self.operation.strip(),"payload":thaw_json_value(p),"timestamp":t.isoformat()};cor=_hash("correlation",base);rid=_hash("request",{**base,"correlation_id":cor})
  if self.correlation_id and self.correlation_id!=cor:raise ValueError("correlation_id mismatch")
  if self.request_id and self.request_id!=rid:raise ValueError("request_id mismatch")
  object.__setattr__(self,"operation",self.operation.strip());object.__setattr__(self,"timestamp",t);object.__setattr__(self,"payload",p);object.__setattr__(self,"metadata",m);object.__setattr__(self,"correlation_id",cor);object.__setattr__(self,"request_id",rid)
 def to_dict(self):return {"request_id":self.request_id,"operation":self.operation,"payload":thaw_json_value(self.payload),"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(request_id=v["request_id"],operation=v["operation"],payload=v["payload"],correlation_id=v["correlation_id"],timestamp=datetime.fromisoformat(v["timestamp"]),metadata=v.get("metadata",{}))
@dataclass(frozen=True,slots=True,kw_only=True)
class TransportResponse:
 response_id:str="";success:bool;result:Mapping[str,JSONValue];error:str;correlation_id:str;timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.success,bool) or not isinstance(self.error,str) or not isinstance(self.correlation_id,str) or not self.correlation_id:raise ValueError("response fields invalid")
  if self.success and self.error:raise ValueError("successful response cannot contain error")
  t=aware_timestamp(self.timestamp);r=freeze_json_mapping("result",self.result);m=freeze_json_mapping("metadata",self.metadata);rid=_hash("response",{"success":self.success,"result":thaw_json_value(r),"error":self.error,"correlation_id":self.correlation_id,"timestamp":t.isoformat()})
  if self.response_id and self.response_id!=rid:raise ValueError("response_id mismatch")
  object.__setattr__(self,"response_id",rid);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"result",r);object.__setattr__(self,"metadata",m)
 def to_dict(self):return {"response_id":self.response_id,"success":self.success,"result":thaw_json_value(self.result),"error":self.error,"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(response_id=v["response_id"],success=v["success"],result=v["result"],error=v["error"],correlation_id=v["correlation_id"],timestamp=datetime.fromisoformat(v["timestamp"]),metadata=v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class TransportExecutionRecord:
 request:TransportRequest;response:TransportResponse;started_timestamp:datetime;completed_timestamp:datetime;duration:Decimal;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.request,TransportRequest) or not isinstance(self.response,TransportResponse):raise ValueError("request and response required")
  s=aware_timestamp(self.started_timestamp);c=aware_timestamp(self.completed_timestamp)
  if c<s:raise ValueError("completion cannot precede start")
  d=Decimal(self.duration)
  if d<0 or d!=Decimal(str((c-s).total_seconds())):raise ValueError("duration inconsistent")
  object.__setattr__(self,"started_timestamp",s);object.__setattr__(self,"completed_timestamp",c);object.__setattr__(self,"duration",d);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request":self.request.to_dict(),"response":self.response.to_dict(),"started_timestamp":self.started_timestamp.isoformat(),"completed_timestamp":self.completed_timestamp.isoformat(),"duration":str(self.duration),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(TransportRequest.from_dict(v["request"]),TransportResponse.from_dict(v["response"]),datetime.fromisoformat(v["started_timestamp"]),datetime.fromisoformat(v["completed_timestamp"]),v["duration"],v.get("metadata",{}))
