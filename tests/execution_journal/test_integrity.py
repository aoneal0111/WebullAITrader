import json
from app.execution_journal import *
from tests.execution_journal.helpers import authorization
def test_truncated_malformed_modified_sequence_and_hash(tmp_path):
 p=tmp_path/"j";j=JsonlExecutionJournal(p);j.append_authorization(authorization());raw=p.read_bytes();p.write_bytes(raw[:-1]);assert j.verify_integrity().status is JournalIntegrityStatus.TRUNCATED
 p.write_bytes(b"{bad}\n");assert j.verify_integrity().status is JournalIntegrityStatus.CORRUPTED
 p.write_bytes(raw);d=json.loads(raw);d["sequence_number"]=2;p.write_text(json.dumps(d)+"\n");assert j.verify_integrity().status is JournalIntegrityStatus.INVALID_SEQUENCE
 d=json.loads(raw);d["payload"]["symbol"]="MSFT";p.write_text(json.dumps(d)+"\n");assert j.verify_integrity().status is JournalIntegrityStatus.HASH_MISMATCH
