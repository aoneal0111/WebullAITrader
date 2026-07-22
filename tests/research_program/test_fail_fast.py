from app.research_program import *
from tests.research_program.helpers import Executor,request,runtime
def test_fail_fast_records_exact_skips():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"study-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2 and tuple(x.status for x in result.studies)==(ResearchProgramStudyStatus.COMPLETED,ResearchProgramStudyStatus.STUDY_FAILED,ResearchProgramStudyStatus.SKIPPED)
    skipped=result.studies[2];assert skipped.index==2 and skipped.identity is req.studies[2].identity and skipped.study_request is req.studies[2].study_request and skipped.study_result is None
    assert skipped.error_type is None and skipped.message=="Skipped because fail-fast policy stopped the research program." and result.status is ResearchProgramStatus.PARTIALLY_COMPLETED
def test_first_failure_is_failed():
    result=runtime(Executor(errors={"study-0":RuntimeError()}))[0].run(request(3,fail_fast=True))
    assert result.status is ResearchProgramStatus.FAILED and result.summary==ResearchProgramSummary(3,1,0,1,2)
