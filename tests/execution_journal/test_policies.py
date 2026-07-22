from dataclasses import FrozenInstanceError
import json,pytest
from app.execution_journal import ExecutionJournalPolicy
def test_roundtrip_frozen():
 p=ExecutionJournalPolicy(metadata={"x":[1]});assert ExecutionJournalPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"fsync_enabled":1},{"maximum_record_bytes":0},{"version":""}])
def test_invalid(x):
 with pytest.raises(ValueError):ExecutionJournalPolicy(**x)
