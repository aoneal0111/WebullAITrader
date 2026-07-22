from app.research_portfolio import *
from tests.research_portfolio.helpers import Executor,request,runtime
def test_fail_fast_records_exact_skips():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"program-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2 and tuple(x.status for x in result.programs)==(ResearchPortfolioProgramStatus.COMPLETED,ResearchPortfolioProgramStatus.PROGRAM_FAILED,ResearchPortfolioProgramStatus.SKIPPED)
    skipped=result.programs[2];assert skipped.index==2 and skipped.identity is req.programs[2].identity and skipped.program_request is req.programs[2].program_request and skipped.program_result is None
    assert skipped.error_type is None and skipped.message=="Skipped because fail-fast policy stopped the research portfolio." and result.status is ResearchPortfolioStatus.PARTIALLY_COMPLETED
def test_first_failure_is_failed():
    result=runtime(Executor(errors={"program-0":RuntimeError()}))[0].run(request(3,fail_fast=True))
    assert result.status is ResearchPortfolioStatus.FAILED and result.summary==ResearchPortfolioSummary(3,1,0,1,2)
