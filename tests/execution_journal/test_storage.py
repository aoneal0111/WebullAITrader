import os
from app.execution_journal import ExecutionJournalPolicy,JsonlExecutionJournal
from tests.execution_journal.helpers import authorization
def test_canonical_newline_and_fsync_switch(tmp_path,monkeypatch):
 calls=[];monkeypatch.setattr(os,"fsync",lambda fd:calls.append(fd));p=tmp_path/"j.jsonl";JsonlExecutionJournal(p).append_authorization(authorization());assert calls and p.read_bytes().endswith(b"\n")
 calls.clear();JsonlExecutionJournal(tmp_path/"n.jsonl",ExecutionJournalPolicy(fsync_enabled=False)).append_authorization(authorization());assert not calls
