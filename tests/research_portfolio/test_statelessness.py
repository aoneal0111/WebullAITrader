from dataclasses import replace
from app.research_portfolio import ResearchPortfolioStatus,ResearchPortfolioProgramStatus
from tests.research_portfolio.helpers import Executor,request,runtime
def test_runtime_has_no_cross_call_state():
    executor=Executor(errors={"program-0":RuntimeError()});engine,_=runtime(executor);failed=engine.run(request(2,fail_fast=True));executor.errors={};success=engine.run(request(2));again=engine.run(request(2))
    assert tuple(x.status for x in failed.programs)==(ResearchPortfolioProgramStatus.PROGRAM_FAILED,ResearchPortfolioProgramStatus.SKIPPED)
    assert success.status is ResearchPortfolioStatus.COMPLETED and success==again and success is not again
    assert failed.programs is not success.programs and success.programs is not again.programs and success.summary is not again.summary and success.criteria is not again.criteria
def test_rejected_disabled_and_fail_fast_do_not_contaminate_later_calls():
    executor=Executor();engine,_=runtime(executor);base=request(2)
    rejected_request=replace(base,programs=(replace(base.programs[0],identity=replace(base.programs[0].identity,program_id="mismatch")),base.programs[1]))
    rejected=engine.run(rejected_request);disabled=engine.run(request(1,enabled=False))
    executor.errors={"program-0":RuntimeError()};stopped=engine.run(request(2,fail_fast=True));executor.errors={};continued=engine.run(request(2,fail_fast=False))
    assert rejected.status is ResearchPortfolioStatus.REJECTED and rejected.programs==()
    assert disabled.status is ResearchPortfolioStatus.DISABLED and stopped.summary.skipped_programs==1
    assert continued.status is ResearchPortfolioStatus.COMPLETED and continued.errors==()
