from dataclasses import dataclass,field
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlsplit
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.webull_protocol_evidence.exceptions import *
class ProtocolClaimCategory(StrEnum):
 ENDPOINT="ENDPOINT";HTTP_METHOD="HTTP_METHOD";REQUEST_HEADER="REQUEST_HEADER";REQUEST_FIELD="REQUEST_FIELD";RESPONSE_HEADER="RESPONSE_HEADER";RESPONSE_FIELD_PATH="RESPONSE_FIELD_PATH";SUCCESS_VALUE="SUCCESS_VALUE";FAILURE_FIELD_PATH="FAILURE_FIELD_PATH";CREDENTIAL_REFERENCE_MAPPING="CREDENTIAL_REFERENCE_MAPPING";DEVICE_IDENTIFIER_BEHAVIOR="DEVICE_IDENTIFIER_BEHAVIOR"
class EvidenceDisposition(StrEnum):SUPPORTS="SUPPORTS";CONTRADICTS="CONTRADICTS";INCONCLUSIVE="INCONCLUSIVE"
class EvidenceSourceClassification(StrEnum):OFFICIAL_DOCUMENTATION="OFFICIAL_DOCUMENTATION";OFFICIAL_CLIENT_BEHAVIOR="OFFICIAL_CLIENT_BEHAVIOR";CONTROLLED_OBSERVATION="CONTROLLED_OBSERVATION";SYNTHETIC_TEST="SYNTHETIC_TEST";THIRD_PARTY_REPORT="THIRD_PARTY_REPORT"
class EvidenceDecision(StrEnum):INSUFFICIENT="INSUFFICIENT";SUPPORTED="SUPPORTED";CONTRADICTED="CONTRADICTED";REJECTED="REJECTED";DISABLED="DISABLED"
def _s(v,n,error=WebullProtocolEvidenceValidationError):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise error(f"{n} must be a non-empty stripped string")
 return v
def _safe_metadata(n,v):
 try:f=freeze_json_mapping(n,v)
 except Exception as exc:raise WebullProtocolEvidenceValidationError(f"{n} must be JSON-compatible") from exc
 banned=("password","passwd","access_token","refresh_token","cookie","authorization_value","secret","session_value","credential_value","account_id","request_body","response_body")
 if any(any(x in k.casefold() for x in banned) for k in f):raise WebullProtocolEvidenceValidationError(f"{n} contains a prohibited field")
 return f
def _safe_value(v,n):
 try:f=freeze_json_mapping(n,{"value":v})["value"]
 except Exception as exc:raise WebullProtocolEvidenceValidationError(f"{n} must be JSON-compatible") from exc
 if isinstance(f,str) and (f.casefold().startswith("bearer ") or "actual-secret" in f.casefold()):raise WebullProtocolEvidenceValidationError(f"{n} contains a prohibited value")
 return f
@dataclass(frozen=True,slots=True)
class WebullProtocolClaim:
 claim_id:str;profile_scope:str;category:ProtocolClaimCategory;subject:str;asserted_value:JSONValue;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"claim_id",_s(self.claim_id,"claim_id",WebullProtocolClaimError));object.__setattr__(self,"profile_scope",_s(self.profile_scope,"profile_scope",WebullProtocolClaimError));object.__setattr__(self,"subject",_s(self.subject,"subject",WebullProtocolClaimError))
  if not isinstance(self.category,ProtocolClaimCategory):raise WebullProtocolClaimError("category must be ProtocolClaimCategory")
  object.__setattr__(self,"asserted_value",_safe_value(self.asserted_value,"asserted_value"));object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"claim_id":self.claim_id,"profile_scope":self.profile_scope,"category":self.category.value,"subject":self.subject,"asserted_value":thaw_json_value(self.asserted_value),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):d=dict(v);d["category"]=ProtocolClaimCategory(d["category"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidenceSource:
 source_id:str;classification:EvidenceSourceClassification;source_reference:str;collection_method:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"source_id",_s(self.source_id,"source_id",WebullProtocolEvidenceRecordError));object.__setattr__(self,"source_reference",_s(self.source_reference,"source_reference",WebullProtocolEvidenceRecordError));object.__setattr__(self,"collection_method",_s(self.collection_method,"collection_method",WebullProtocolEvidenceRecordError))
  if not isinstance(self.classification,EvidenceSourceClassification):raise WebullProtocolEvidenceRecordError("classification must be EvidenceSourceClassification")
  parsed=urlsplit(self.source_reference)
  if parsed.scheme and parsed.username:raise WebullProtocolEvidenceRecordError("source reference cannot contain embedded credentials")
  object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"source_id":self.source_id,"classification":self.classification.value,"source_reference":self.source_reference,"collection_method":self.collection_method,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):d=dict(v);d["classification"]=EvidenceSourceClassification(d["classification"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidenceRecord:
 evidence_id:str;claim_id:str;source:WebullProtocolEvidenceSource;disposition:EvidenceDisposition;observed_value:JSONValue;reproducible:bool;independence_group:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"evidence_id",_s(self.evidence_id,"evidence_id",WebullProtocolEvidenceRecordError));object.__setattr__(self,"claim_id",_s(self.claim_id,"claim_id",WebullProtocolEvidenceRecordError));object.__setattr__(self,"independence_group",_s(self.independence_group,"independence_group",WebullProtocolEvidenceRecordError))
  if not isinstance(self.source,WebullProtocolEvidenceSource):raise WebullProtocolEvidenceRecordError("source must be WebullProtocolEvidenceSource")
  if not isinstance(self.disposition,EvidenceDisposition):raise WebullProtocolEvidenceRecordError("disposition must be EvidenceDisposition")
  if not isinstance(self.reproducible,bool):raise WebullProtocolEvidenceRecordError("reproducible must be boolean")
  object.__setattr__(self,"observed_value",_safe_value(self.observed_value,"observed_value"));object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"evidence_id":self.evidence_id,"claim_id":self.claim_id,"source":self.source.to_dict(),"disposition":self.disposition.value,"observed_value":thaw_json_value(self.observed_value),"reproducible":self.reproducible,"independence_group":self.independence_group,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):d=dict(v);d["source"]=WebullProtocolEvidenceSource.from_dict(d["source"]);d["disposition"]=EvidenceDisposition(d["disposition"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidenceBundle:
 claims:tuple[WebullProtocolClaim,...];records:tuple[WebullProtocolEvidenceRecord,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not isinstance(self.claims,tuple) or any(not isinstance(x,WebullProtocolClaim) for x in self.claims):raise WebullProtocolEvidenceValidationError("claims must be an immutable claim tuple")
  if not isinstance(self.records,tuple) or any(not isinstance(x,WebullProtocolEvidenceRecord) for x in self.records):raise WebullProtocolEvidenceValidationError("records must be an immutable evidence tuple")
  object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"claims":[x.to_dict() for x in self.claims],"records":[x.to_dict() for x in self.records],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(tuple(WebullProtocolClaim.from_dict(x) for x in v["claims"]),tuple(WebullProtocolEvidenceRecord.from_dict(x) for x in v["records"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidenceAssessment:
 claim_id:str;decision:EvidenceDecision;eligible_for_profile_use:bool;supporting_record_ids:tuple[str,...];contradicting_record_ids:tuple[str,...];inconclusive_record_ids:tuple[str,...];independent_support_group_count:int;reproducible_support_count:int;criteria:Mapping[str,bool];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):object.__setattr__(self,"criteria",freeze_json_mapping("criteria",self.criteria));object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"claim_id":self.claim_id,"decision":self.decision.value,"eligible_for_profile_use":self.eligible_for_profile_use,"supporting_record_ids":list(self.supporting_record_ids),"contradicting_record_ids":list(self.contradicting_record_ids),"inconclusive_record_ids":list(self.inconclusive_record_ids),"independent_support_group_count":self.independent_support_group_count,"reproducible_support_count":self.reproducible_support_count,"criteria":thaw_json_value(self.criteria),"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class WebullProtocolEvidenceRegistrationResult:
 registry:object;added_claim_ids:tuple[str,...];added_evidence_ids:tuple[str,...];total_claims:int;total_records:int;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  if not callable(getattr(self.registry,"register",None)) or not callable(getattr(self.registry,"assess",None)):raise WebullProtocolEvidenceValidationError("registry result contains invalid registry")
  object.__setattr__(self,"metadata",_safe_metadata("metadata",self.metadata))
 def to_dict(self):return {"added_claim_ids":list(self.added_claim_ids),"added_evidence_ids":list(self.added_evidence_ids),"total_claims":self.total_claims,"total_records":self.total_records,"metadata":thaw_json_value(self.metadata)}
