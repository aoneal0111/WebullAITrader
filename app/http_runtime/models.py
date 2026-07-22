from __future__ import annotations
import hashlib,json
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.http_runtime.models_base import HTTPMethod
from app.trade_proposals.models import aware_timestamp
def _canon(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False)
def _hash(kind,v):return hashlib.sha256(_canon({"kind":kind,**v}).encode()).hexdigest()
def _headers(v):
 if not isinstance(v,Mapping):raise ValueError("headers must be mapping")
 d={}
 for k,x in v.items():
  if not isinstance(k,str) or not k.strip() or not isinstance(x,str):raise ValueError("header names and values must be strings")
  key=k.strip().lower()
  if key in d:raise ValueError("header names must be unique")
  d[key]=x
 return MappingProxyType(d)
@dataclass(frozen=True,slots=True,kw_only=True)
class HTTPRequest:
 request_id:str="";method:HTTPMethod;url:str;headers:Mapping[str,str];body:Mapping[str,JSONValue];correlation_id:str="";timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.method,HTTPMethod) or not isinstance(self.url,str) or not self.url.strip():raise ValueError("method and url required")
  t=aware_timestamp(self.timestamp);h=_headers(self.headers);b=freeze_json_mapping("body",self.body);m=freeze_json_mapping("metadata",self.metadata);base={"method":self.method.value,"url":self.url.strip(),"headers":dict(h),"body":thaw_json_value(b),"timestamp":t.isoformat()};cor=_hash("http_correlation",base);rid=_hash("http_request",{**base,"correlation_id":cor})
  if self.request_id and self.request_id!=rid:raise ValueError("request ID mismatch")
  if self.correlation_id and self.correlation_id!=cor:raise ValueError("correlation ID mismatch")
  object.__setattr__(self,"request_id",rid);object.__setattr__(self,"correlation_id",cor);object.__setattr__(self,"url",self.url.strip());object.__setattr__(self,"timestamp",t);object.__setattr__(self,"headers",h);object.__setattr__(self,"body",b);object.__setattr__(self,"metadata",m)
 def to_dict(self):return {"request_id":self.request_id,"method":self.method.value,"url":self.url,"headers":dict(self.headers),"body":thaw_json_value(self.body),"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(request_id=v["request_id"],method=HTTPMethod(v["method"]),url=v["url"],headers=v["headers"],body=v["body"],correlation_id=v["correlation_id"],timestamp=datetime.fromisoformat(v["timestamp"]),metadata=v.get("metadata",{}))
@dataclass(frozen=True,slots=True,kw_only=True)
class HTTPResponse:
 response_id:str="";status_code:int;headers:Mapping[str,str];body:Mapping[str,JSONValue];correlation_id:str;timestamp:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if isinstance(self.status_code,bool) or not isinstance(self.status_code,int) or not 100<=self.status_code<=599:raise ValueError("status_code invalid")
  if not isinstance(self.correlation_id,str) or not self.correlation_id:raise ValueError("correlation_id required")
  t=aware_timestamp(self.timestamp);h=_headers(self.headers);b=freeze_json_mapping("body",self.body);m=freeze_json_mapping("metadata",self.metadata);rid=_hash("http_response",{"status_code":self.status_code,"headers":dict(h),"body":thaw_json_value(b),"correlation_id":self.correlation_id,"timestamp":t.isoformat()})
  if self.response_id and self.response_id!=rid:raise ValueError("response ID mismatch")
  object.__setattr__(self,"response_id",rid);object.__setattr__(self,"timestamp",t);object.__setattr__(self,"headers",h);object.__setattr__(self,"body",b);object.__setattr__(self,"metadata",m)
 def to_dict(self):return {"response_id":self.response_id,"status_code":self.status_code,"headers":dict(self.headers),"body":thaw_json_value(self.body),"correlation_id":self.correlation_id,"timestamp":self.timestamp.isoformat(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(response_id=v["response_id"],status_code=v["status_code"],headers=v["headers"],body=v["body"],correlation_id=v["correlation_id"],timestamp=datetime.fromisoformat(v["timestamp"]),metadata=v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class HTTPExecutionRecord:
 request:HTTPRequest;response:HTTPResponse;started_timestamp:datetime;completed_timestamp:datetime;duration:Decimal;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.request,HTTPRequest) or not isinstance(self.response,HTTPResponse):raise ValueError("request and response required")
  s=aware_timestamp(self.started_timestamp);c=aware_timestamp(self.completed_timestamp);d=Decimal(self.duration)
  if c<s or d!=Decimal(str((c-s).total_seconds())):raise ValueError("duration invalid")
  object.__setattr__(self,"started_timestamp",s);object.__setattr__(self,"completed_timestamp",c);object.__setattr__(self,"duration",d);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request":self.request.to_dict(),"response":self.response.to_dict(),"started_timestamp":self.started_timestamp.isoformat(),"completed_timestamp":self.completed_timestamp.isoformat(),"duration":str(self.duration),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(HTTPRequest.from_dict(v["request"]),HTTPResponse.from_dict(v["response"]),datetime.fromisoformat(v["started_timestamp"]),datetime.fromisoformat(v["completed_timestamp"]),v["duration"],v.get("metadata",{}))
