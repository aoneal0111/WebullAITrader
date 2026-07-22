from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.http_runtime import HTTPMethod
from app.trade_proposals.models import aware_timestamp
def _text_map(name,v):
 if not isinstance(v,Mapping):raise ValueError(f"{name} must be mapping")
 d={}
 for k,x in v.items():
  if not isinstance(k,str) or not k or not isinstance(x,str):raise ValueError(f"{name} must contain strings")
  if k in d:raise ValueError(f"duplicate {name} key")
  d[k]=x
 return MappingProxyType(d)
@dataclass(frozen=True,slots=True)
class SerializedHTTPRequest:
 request_id:str;method:HTTPMethod;url:str;headers:Mapping[str,str];body:Mapping[str,JSONValue];correlation_id:str;timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not all(isinstance(x,str) and x for x in (self.request_id,self.url,self.correlation_id)) or not isinstance(self.method,HTTPMethod):raise ValueError("serialized request identifiers invalid")
  object.__setattr__(self,"headers",_text_map("headers",self.headers));object.__setattr__(self,"body",freeze_json_mapping("body",self.body));object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"method":self.method.value,"url":self.url,"headers":dict(self.headers),"body":thaw_json_value(self.body),"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["request_id"],HTTPMethod(v["method"]),v["url"],v["headers"],v["body"],v["correlation_id"],datetime.fromisoformat(v["timestamp"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class SerializedHTTPResponse:
 status_code:int;headers:Mapping[str,str];body:Mapping[str,JSONValue];correlation_id:str;timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if isinstance(self.status_code,bool) or not isinstance(self.status_code,int) or not 100<=self.status_code<=599:raise ValueError("status code invalid")
  if not isinstance(self.correlation_id,str) or not self.correlation_id:raise ValueError("correlation required")
  object.__setattr__(self,"headers",_text_map("headers",self.headers));object.__setattr__(self,"body",freeze_json_mapping("body",self.body));object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"status_code":self.status_code,"headers":dict(self.headers),"body":thaw_json_value(self.body),"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["status_code"],v["headers"],v["body"],v["correlation_id"],datetime.fromisoformat(v["timestamp"]),v.get("metadata",{}))
