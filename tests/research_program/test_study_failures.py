from app.research_program import *
from tests.research_program.helpers import Executor,request,runtime
def test_continue_after_safe_failure():
    req=request(3);engine,executor=runtime(Executor(errors={"study-1":ValueError("secret")}));result=engine.run(req)
    assert len(executor.calls)==3 and tuple(x.status for x in result.studies)==(ResearchProgramStudyStatus.COMPLETED,ResearchProgramStudyStatus.STUDY_FAILED,ResearchProgramStudyStatus.COMPLETED)
    failed=result.studies[1];assert failed.identity is req.studies[1].identity and failed.study_request is req.studies[1].study_request and failed.study_result is None
    assert failed.error_type=="ValueError" and failed.message=="Research study invocation failed." and "secret" not in str(result.to_dict())
def test_invalid_study_result():
    result=runtime(Executor(callback=lambda ignored:object()))[0].run(request(2))
    assert result.status is ResearchProgramStatus.FAILED and all(x.error_type=="InvalidResearchStudyResult" and x.message=="Research study returned an invalid result." for x in result.studies)
def test_multiple_continue_mode_failures_preserve_positions_and_counts():
    req=request(4);engine,executor=runtime(Executor(errors={"study-0":RuntimeError(),"study-2":LookupError()}));result=engine.run(req)
    assert len(executor.calls)==4 and tuple(x.index for x in result.studies)==(0,1,2,3)
    assert tuple(x.status for x in result.studies)==(ResearchProgramStudyStatus.STUDY_FAILED,ResearchProgramStudyStatus.COMPLETED,ResearchProgramStudyStatus.STUDY_FAILED,ResearchProgramStudyStatus.COMPLETED)
    assert result.summary==ResearchProgramSummary(4,4,2,2,0) and result.status is ResearchProgramStatus.PARTIALLY_COMPLETED
def test_all_exception_failures_map_to_failed_without_retries():
    req=request(2);engine,executor=runtime(Executor(errors={"study-0":RuntimeError(),"study-1":RuntimeError()}));result=engine.run(req)
    assert result.status is ResearchProgramStatus.FAILED and result.summary==ResearchProgramSummary(2,2,0,2,0)
    assert executor.calls==[x.study_request for x in req.studies]
