from __future__ import annotations
import hashlib,json
from dataclasses import dataclass,field
from datetime import datetime
from enum import StrEnum
from typing import Mapping,Any
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.models import aware_timestamp
class JournalRecordType(StrEnum):AUTHORIZATION="AUTHORIZATION";EXECUTION="EXECUTION"
class JournalIntegrityStatus(StrEnum):VALID="VALID";EMPTY="EMPTY";CORRUPTED="CORRUPTED";TRUNCATED="TRUNCATED";INVALID_SEQUENCE="INVALID_SEQUENCE";HASH_MISMATCH="HASH_MISMATCH"
JOURNAL_VERSION="execution_journal_v1"
def canonical_json(v):return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
def hashes(sequence_number,record_type,entity_id,timestamp,payload,previous_record_hash,policy_version,journal_version=JOURNAL_VERSION):
    base={"sequence_number":sequence_number,"record_type":record_type.value,"entity_id":entity_id,"timestamp":timestamp.isoformat(),"payload":payload,"previous_record_hash":previous_record_hash,"policy_version":policy_version,"journal_version":journal_version}
    rh=hashlib.sha256(canonical_json(base).encode()).hexdigest();ph=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    rid=hashlib.sha256(canonical_json({"record_type":record_type.value,"entity_id":entity_id,"timestamp":timestamp.isoformat(),"payload_hash":ph,"previous_record_hash":previous_record_hash,"sequence_number":sequence_number}).encode()).hexdigest();return rid,rh
@dataclass(frozen=True,slots=True)
class JournalRecord:
    sequence_number:int;record_id:str;record_type:JournalRecordType;entity_id:str;timestamp:datetime;payload:Mapping[str,JSONValue];previous_record_hash:str;record_hash:str;policy_version:str;journal_version:str=JOURNAL_VERSION;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if isinstance(self.sequence_number,bool) or not isinstance(self.sequence_number,int) or self.sequence_number<1:raise ValueError("sequence_number must begin at one")
        for n in ("record_id","entity_id","record_hash","policy_version","journal_version"):
            if not isinstance(getattr(self,n),str) or not getattr(self,n).strip():raise ValueError(f"{n} must be nonempty")
        if not isinstance(self.record_type,JournalRecordType):raise ValueError("record_type invalid")
        object.__setattr__(self,"timestamp",aware_timestamp(self.timestamp));object.__setattr__(self,"payload",freeze_json_mapping("payload",self.payload));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
        if self.sequence_number==1 and self.previous_record_hash!="":raise ValueError("first record previous hash must be empty")
        if self.sequence_number>1 and (not isinstance(self.previous_record_hash,str) or not self.previous_record_hash):raise ValueError("later records require previous hash")
        rid,rh=hashes(self.sequence_number,self.record_type,self.entity_id,self.timestamp,thaw_json_value(self.payload),self.previous_record_hash,self.policy_version,self.journal_version)
        if self.record_id!=rid:raise ValueError("record ID mismatch")
        if self.record_hash!=rh:raise ValueError("record hash mismatch")
    def to_dict(self):return {"sequence_number":self.sequence_number,"record_id":self.record_id,"record_type":self.record_type.value,"entity_id":self.entity_id,"timestamp":self.timestamp.isoformat(),"payload":thaw_json_value(self.payload),"previous_record_hash":self.previous_record_hash,"record_hash":self.record_hash,"policy_version":self.policy_version,"journal_version":self.journal_version,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:
            d=dict(v);d["record_type"]=JournalRecordType(d["record_type"]);d["timestamp"]=datetime.fromisoformat(d["timestamp"]);return cls(**d)
        except (TypeError,ValueError,KeyError) as e:raise ValueError("Unable to deserialize journal record") from e
@dataclass(frozen=True,slots=True)
class JournalIntegrityResult:
    status:JournalIntegrityStatus;record_count:int;message:str
    def __post_init__(self):
        if not isinstance(self.status,JournalIntegrityStatus) or self.record_count<0 or not self.message:raise ValueError("invalid integrity result")
@dataclass(frozen=True,slots=True)
class JournalRecoveryState:
    records:tuple[JournalRecord,...];authorization_ids:tuple[str,...];execution_ids:tuple[str,...];last_record_hash:str;next_sequence_number:int;integrity_status:JournalIntegrityStatus;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.records,tuple) or not all(isinstance(x,JournalRecord) for x in self.records):raise ValueError("records must be immutable JournalRecords")
        for n in ("authorization_ids","execution_ids"):
            if not isinstance(getattr(self,n),tuple):raise ValueError(f"{n} must be tuple")
        if self.next_sequence_number!=len(self.records)+1:raise ValueError("next sequence inconsistent")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
