import multiprocessing
from app.broker_execution import BrokerExecutionAuthorization
from app.execution_journal import *
from tests.execution_journal.helpers import authorization

def _append_worker(path,authorization_data,start,queue):
    start.wait(10)
    try:
        record=JsonlExecutionJournal(path).append_authorization(BrokerExecutionAuthorization.from_dict(authorization_data));queue.put(("ok",record.sequence_number))
    except JournalDuplicateError:queue.put(("duplicate",None))
    except Exception as exc:queue.put(("error",type(exc).__name__))

def _run_workers(path,items):
    ctx=multiprocessing.get_context("spawn");start=ctx.Event();queue=ctx.Queue();processes=[ctx.Process(target=_append_worker,args=(str(path),x.to_dict(),start,queue)) for x in items]
    for p in processes:p.start()
    start.set();results=[queue.get(timeout=15) for _ in processes]
    for p in processes:
        p.join(10)
        if p.is_alive():p.terminate();p.join(5)
        assert p.exitcode==0
    queue.close();queue.join_thread();return results

def test_same_authorization_exactly_one_writer(tmp_path):
    item=authorization();results=_run_workers(tmp_path/"journal",(item,item));assert sorted(x[0] for x in results)==["duplicate","ok"]
    journal=JsonlExecutionJournal(tmp_path/"journal");assert journal.verify_integrity().status is JournalIntegrityStatus.VALID;assert len(journal.load_records())==1

def test_different_authorizations_serialize_with_hash_chain(tmp_path):
    from app.broker_execution import ExecutionSafetyGate
    from tests.broker_execution.helpers import request
    first=authorization();second=ExecutionSafetyGate().authorize(request(request_fingerprint="fp-2"))
    results=_run_workers(tmp_path/"journal",(first,second));assert sorted(x[1] for x in results)==[1,2]
    records=JsonlExecutionJournal(tmp_path/"journal").load_records();assert records[1].previous_record_hash==records[0].record_hash
