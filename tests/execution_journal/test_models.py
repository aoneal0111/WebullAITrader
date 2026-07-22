from dataclasses import FrozenInstanceError
import json,pytest
from app.execution_journal import JournalRecord
from tests.execution_journal.helpers import authorization
def test_record_roundtrip_frozen(tmp_path):
 from app.execution_journal import JsonlExecutionJournal
 r=JsonlExecutionJournal(tmp_path/"j.jsonl").append_authorization(authorization());assert JournalRecord.from_dict(r.to_dict())==r;json.dumps(r.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):r.sequence_number=2
