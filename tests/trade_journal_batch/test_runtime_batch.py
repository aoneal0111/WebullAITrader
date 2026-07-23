from app.trade_journal_batch import *
from tests.trade_journal_batch.helpers import request,runtime
def test_one_cycle_success():
    engine,appender,factory=runtime();result=engine.run(request(1))
    assert result.status is TradeJournalBatchStatus.COMPLETED and len(appender.calls)==len(factory.calls)==1
    assert result.progress.completed_count==1 and result.final_journal.total_entries==1
def test_multi_cycle_order_and_exact_state_continuity():
    req=request(3);engine,appender,factory=runtime();result=engine.run(req)
    assert tuple(x.cycle.identity.cycle_id for x in appender.calls)==tuple(x.cycle.identity.cycle_id for x in req.items)
    assert appender.calls[0].state is req.initial_journal
    assert appender.calls[1].state is result.item_results[0].append_result.state
    assert appender.calls[2].state is result.item_results[1].append_result.state
    assert result.final_journal is result.item_results[-1].append_result.state and req.initial_journal.total_entries==0
def test_exact_append_results_preserved():
    result=runtime()[0].run(request(2))
    assert all(x.status is TradeJournalBatchItemStatus.COMPLETED and x.append_result is not None for x in result.item_results)
def test_repeated_runs_are_deterministic_and_stateless():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and a.to_dict()==b.to_dict()
