from dataclasses import replace
from app.research_program import ResearchProgramStatus,ResearchProgramStudyStatus
from tests.research_program.helpers import Executor,request,runtime
def test_runtime_has_no_cross_call_state():
    executor=Executor(errors={"study-0":RuntimeError()});engine,_=runtime(executor);failed=engine.run(request(2,fail_fast=True));executor.errors={};success=engine.run(request(2));again=engine.run(request(2))
    assert tuple(x.status for x in failed.studies)==(ResearchProgramStudyStatus.STUDY_FAILED,ResearchProgramStudyStatus.SKIPPED)
    assert success.status is ResearchProgramStatus.COMPLETED and success==again and success is not again
    assert failed.studies is not success.studies and success.studies is not again.studies and success.summary is not again.summary and success.criteria is not again.criteria
def test_rejected_disabled_and_fail_fast_runs_do_not_contaminate_later_calls():
    executor=Executor();engine,_=runtime(executor);base=request(2)
    rejected_request=replace(base,studies=(replace(base.studies[0],identity=replace(base.studies[0].identity,study_id="mismatch")),base.studies[1]))
    rejected=engine.run(rejected_request);disabled=engine.run(request(1,enabled=False))
    executor.errors={"study-0":RuntimeError()};stopped=engine.run(request(2,fail_fast=True));executor.errors={};continued=engine.run(request(2,fail_fast=False))
    assert rejected.status is ResearchProgramStatus.REJECTED and disabled.status is ResearchProgramStatus.DISABLED
    assert stopped.summary.skipped_studies==1 and continued.status is ResearchProgramStatus.COMPLETED and continued.errors==()
