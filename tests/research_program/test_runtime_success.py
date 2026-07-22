from dataclasses import replace
from app.research_program import *
from app.research_study import ResearchStudyStatus
from tests.research_program.helpers import Executor,request,runtime
from tests.research_study.helpers import runtime as study_runtime
def test_order_and_exact_object_continuity():
    req=request(3);known=tuple(study_runtime()[0].run(x.study_request) for x in req.studies);by_id={x.identity.study_id:x for x in known}
    engine,executor=runtime(Executor(callback=lambda child:by_id[child.identity.study_id]));result=engine.run(req)
    assert all(executor.calls[i] is req.studies[i].study_request for i in range(3))
    assert all(result.studies[i].study_result is known[i] and result.studies[i].identity is req.studies[i].identity for i in range(3))
    assert tuple(x.index for x in result.studies)==(0,1,2) and len(executor.calls)==3
    assert result.status is ResearchProgramStatus.COMPLETED and result.summary==ResearchProgramSummary(3,3,3,0,0)
    assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_all_valid_child_statuses_complete_and_do_not_fail_fast():
    for status in ResearchStudyStatus:
        calls=[]
        def callback(req,status=status):calls.append(req);return replace(study_runtime()[0].run(req),status=status)
        result=runtime(Executor(callback=callback))[0].run(request(2,fail_fast=True))
        assert len(calls)==2 and all(x.status is ResearchProgramStudyStatus.COMPLETED for x in result.studies) and result.status is ResearchProgramStatus.COMPLETED
def test_repeatability():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and a is not b and a.studies is not b.studies and serialize_result(a)==serialize_result(b)
