from app.research_portfolio import *
from tests.research_portfolio.helpers import Executor,request,runtime
def test_continue_after_safe_failure():
    req=request(3);engine,executor=runtime(Executor(errors={"program-1":ValueError("secret")}));result=engine.run(req)
    assert len(executor.calls)==3 and tuple(x.status for x in result.programs)==(ResearchPortfolioProgramStatus.COMPLETED,ResearchPortfolioProgramStatus.PROGRAM_FAILED,ResearchPortfolioProgramStatus.COMPLETED)
    failed=result.programs[1];assert failed.identity is req.programs[1].identity and failed.program_request is req.programs[1].program_request and failed.program_result is None
    assert failed.error_type=="ValueError" and failed.message=="Research program invocation failed." and "secret" not in str(result.to_dict())
def test_invalid_and_all_failed_results():
    invalid=runtime(Executor(callback=lambda ignored:object()))[0].run(request(2))
    assert invalid.status is ResearchPortfolioStatus.FAILED and all(x.error_type=="InvalidResearchProgramResult" and x.message=="Research program returned an invalid result." for x in invalid.programs)
    failed=runtime(Executor(errors={"program-0":RuntimeError(),"program-1":RuntimeError()}))[0].run(request(2));assert failed.status is ResearchPortfolioStatus.FAILED
def test_multiple_continue_failures_preserve_order_counts_and_no_retries():
    req=request(4);engine,executor=runtime(Executor(errors={"program-0":RuntimeError(),"program-2":LookupError()}));result=engine.run(req)
    assert executor.calls==[x.program_request for x in req.programs] and tuple(x.index for x in result.programs)==(0,1,2,3)
    assert tuple(x.status for x in result.programs)==(ResearchPortfolioProgramStatus.PROGRAM_FAILED,ResearchPortfolioProgramStatus.COMPLETED,ResearchPortfolioProgramStatus.PROGRAM_FAILED,ResearchPortfolioProgramStatus.COMPLETED)
    assert result.summary==ResearchPortfolioSummary(4,4,2,2,0) and result.status is ResearchPortfolioStatus.PARTIALLY_COMPLETED
