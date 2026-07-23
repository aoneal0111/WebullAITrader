import pytest
from app.execution_journal import *
from tests.execution_journal.helpers import authorization,execution
def test_append_chain_recovery_duplicates(tmp_path):
 j=JsonlExecutionJournal(tmp_path/"j.jsonl");a=authorization();e=execution(a);r1=j.append_authorization(a);r2=j.append_execution(e);assert r1.sequence_number==1 and r2.sequence_number==2 and r2.previous_record_hash==r1.record_hash
 s=j.recover();assert s.authorization_ids==(a.authorization_id,) and s.execution_ids==(e.execution_id,) and s.next_sequence_number==3
 with pytest.raises(JournalDuplicateError):j.append_authorization(a)
 with pytest.raises(JournalDuplicateError):j.append_execution(e)
def test_empty(tmp_path):assert JsonlExecutionJournal(tmp_path/"none").verify_integrity().status is JournalIntegrityStatus.EMPTY
