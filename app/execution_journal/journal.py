from __future__ import annotations
import json
from datetime import datetime
from app.broker_execution import BrokerExecutionAuthorization
from app.execution_journal.models import *
from app.execution_journal.policies import ExecutionJournalPolicy
from app.execution_journal.storage import JsonlStorage,JournalDuplicateError,JournalIntegrityError
from app.paper_broker import PaperBrokerExecutionResult,PaperBrokerExecutionStatus,PaperBrokerState
from app.trade_proposals.models import aware_timestamp

class JsonlExecutionJournal:
    def __init__(self,path,policy:ExecutionJournalPolicy|None=None):
        self.policy=policy or ExecutionJournalPolicy()
        if not isinstance(self.policy,ExecutionJournalPolicy):raise ValueError("policy must be ExecutionJournalPolicy")
        self.storage=JsonlStorage(path)
    def verify_integrity(self):
        raw=self.storage.read_bytes()
        if not raw:return JournalIntegrityResult(JournalIntegrityStatus.EMPTY,0,"journal is empty")
        if not raw.endswith(b"\n"):return JournalIntegrityResult(JournalIntegrityStatus.TRUNCATED,0,"final record is not newline terminated")
        parsed=[]
        for i,line in enumerate(raw.splitlines(),1):
            try:v=json.loads(line)
            except (json.JSONDecodeError,UnicodeDecodeError):return JournalIntegrityResult(JournalIntegrityStatus.CORRUPTED,len(parsed),f"invalid JSON at line {i}")
            if not isinstance(v,dict):return JournalIntegrityResult(JournalIntegrityStatus.CORRUPTED,len(parsed),f"invalid record at line {i}")
            if v.get("sequence_number")!=i:return JournalIntegrityResult(JournalIntegrityStatus.INVALID_SEQUENCE,len(parsed),f"invalid sequence at line {i}")
            if (i==1 and v.get("previous_record_hash")!="") or (i>1 and v.get("previous_record_hash")!=parsed[-1].record_hash):return JournalIntegrityResult(JournalIntegrityStatus.HASH_MISMATCH,len(parsed),f"broken hash chain at line {i}")
            try:r=JournalRecord.from_dict(v)
            except ValueError:return JournalIntegrityResult(JournalIntegrityStatus.HASH_MISMATCH,len(parsed),f"record hash mismatch at line {i}")
            required=("authorization_id","request_fingerprint","proposal_id") if r.record_type is JournalRecordType.AUTHORIZATION else ("execution_id","authorization_id","proposal_id")
            if r.entity_id!=r.payload.get(required[0]) or any(x not in r.payload for x in required):return JournalIntegrityResult(JournalIntegrityStatus.CORRUPTED,len(parsed),f"payload structure mismatch at line {i}")
            parsed.append(r)
        ids=[r.record_id for r in parsed];a=[r.entity_id for r in parsed if r.record_type is JournalRecordType.AUTHORIZATION];e=[r.entity_id for r in parsed if r.record_type is JournalRecordType.EXECUTION]
        if len(ids)!=len(set(ids)) or len(a)!=len(set(a)) or len(e)!=len(set(e)):return JournalIntegrityResult(JournalIntegrityStatus.CORRUPTED,len(parsed),"duplicate identifiers")
        return JournalIntegrityResult(JournalIntegrityStatus.VALID,len(parsed),"journal is valid")
    def recover(self):
        integrity=self.verify_integrity()
        if integrity.status is JournalIntegrityStatus.EMPTY:
            if not self.policy.allow_empty_journal:raise JournalIntegrityError("empty journal is not allowed")
            return JournalRecoveryState((),(),(),"",1,JournalIntegrityStatus.EMPTY,{"deterministic":True})
        if integrity.status is not JournalIntegrityStatus.VALID:raise JournalIntegrityError(f"journal integrity failure: {integrity.status.value}: {integrity.message}")
        records=tuple(JournalRecord.from_dict(json.loads(x)) for x in self.storage.read_bytes().splitlines())
        return JournalRecoveryState(records,tuple(r.entity_id for r in records if r.record_type is JournalRecordType.AUTHORIZATION),tuple(r.entity_id for r in records if r.record_type is JournalRecordType.EXECUTION),records[-1].record_hash,len(records)+1,JournalIntegrityStatus.VALID,{"deterministic":True})
    def load_records(self):return self.recover().records
    def load_authorization_ids(self):return self.recover().authorization_ids
    def load_execution_ids(self):return self.recover().execution_ids
    def contains_authorization_id(self,x):return x in self.load_authorization_ids()
    def contains_execution_id(self,x):return x in self.load_execution_ids()
    def append_authorization(self,a):
        if not isinstance(a,BrokerExecutionAuthorization):raise ValueError("authorization must be BrokerExecutionAuthorization")
        return self._append(JournalRecordType.AUTHORIZATION,a.authorization_id,a.timestamp,_authorization_payload(a))
    def append_execution(self,e):
        if not isinstance(e,PaperBrokerExecutionResult):raise ValueError("execution must be PaperBrokerExecutionResult")
        return self._append(JournalRecordType.EXECUTION,e.execution_id,e.timestamp,_execution_payload(e))
    def _append(self,typ,entity,timestamp,payload):
        state=self.recover()
        if self.policy.reject_duplicates:
            if typ is JournalRecordType.AUTHORIZATION and entity in state.authorization_ids:raise JournalDuplicateError(f"duplicate authorization: {entity}")
            if typ is JournalRecordType.EXECUTION and entity in state.execution_ids:raise JournalDuplicateError(f"duplicate execution: {entity}")
        seq=state.next_sequence_number;rid,rh=hashes(seq,typ,entity,timestamp,payload,state.last_record_hash,self.policy.version)
        if any(x.record_id==rid for x in state.records):raise JournalDuplicateError(f"duplicate record: {rid}")
        record=JournalRecord(seq,rid,typ,entity,timestamp,payload,state.last_record_hash,rh,self.policy.version)
        self.storage.append(record.to_dict(),self.policy.fsync_enabled,self.policy.maximum_record_bytes);return record

def _authorization_payload(a):return {k:v for k,v in a.to_dict().items() if k in ("authorization_id","request_fingerprint","proposal_id","symbol","direction","quantity","entry_price","order_notional","projected_symbol_position","mode","timestamp","decision","reason","policy_version","safety_engine_version")}
def _execution_payload(e):return {k:v for k,v in e.to_dict().items() if k in ("execution_id","authorization_id","proposal_id","request_fingerprint","symbol","direction","quantity_requested","quantity_filled","entry_price","fill_price","filled_notional","mode","timestamp","status","rejection_reason","policy_version","adapter_version")}
def paper_broker_state_from_recovery(state:JournalRecoveryState,timestamp:datetime):
    timestamp=aware_timestamp(timestamp);ids=[]
    for r in state.records:
        if r.record_type is JournalRecordType.EXECUTION and r.payload.get("status") in {PaperBrokerExecutionStatus.FILLED.value,PaperBrokerExecutionStatus.ACKNOWLEDGED.value,PaperBrokerExecutionStatus.DUPLICATE.value}:
            aid=r.payload["authorization_id"]
            if aid not in ids:ids.append(aid)
    return PaperBrokerState(timestamp,tuple(ids),{"journal_integrity":state.integrity_status.value})
def authorization_fingerprints(state:JournalRecoveryState):return tuple(r.payload["request_fingerprint"] for r in state.records if r.record_type is JournalRecordType.AUTHORIZATION)
