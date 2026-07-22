from dataclasses import replace
from app.research_portfolio import *
from app.research_program import ResearchProgramStatus
from tests.research_portfolio.helpers import Executor,request,runtime
from tests.research_program.helpers import runtime as program_runtime
def test_order_and_exact_object_continuity():
    req=request(3);known=tuple(program_runtime()[0].run(x.program_request) for x in req.programs);by_id={x.identity.program_id:x for x in known}
    engine,executor=runtime(Executor(callback=lambda child:by_id[child.identity.program_id]));result=engine.run(req)
    assert len(executor.calls)==3 and all(executor.calls[i] is req.programs[i].program_request for i in range(3))
    assert all(result.programs[i].program_result is known[i] and result.programs[i].identity is req.programs[i].identity for i in range(3))
    assert tuple(x.index for x in result.programs)==(0,1,2) and result.status is ResearchPortfolioStatus.COMPLETED and result.summary==ResearchPortfolioSummary(3,3,3,0,0)
    assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_all_valid_child_statuses_complete_and_do_not_fail_fast():
    for status in ResearchProgramStatus:
        calls=[]
        def callback(req,status=status):calls.append(req);return replace(program_runtime()[0].run(req),status=status)
        result=runtime(Executor(callback=callback))[0].run(request(2,fail_fast=True))
        assert len(calls)==2 and result.status is ResearchPortfolioStatus.COMPLETED and all(x.status is ResearchPortfolioProgramStatus.COMPLETED for x in result.programs)
def test_repeatability():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and a is not b and a.programs is not b.programs and a.summary is not b.summary and a.criteria is not b.criteria and serialize_result(a)==serialize_result(b)
