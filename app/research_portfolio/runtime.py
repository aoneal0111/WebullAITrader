from app.research_program import ResearchProgramResult
from app.research_portfolio.models import *
from app.research_portfolio.validation import validate_dependencies,validate_request
class ResearchPortfolioRuntime:
    def __init__(self,program_executor):validate_dependencies(program_executor);self._executor=program_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ResearchPortfolioStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ResearchPortfolioStatus.REJECTED,(),False,errors)
        if not request.programs:return self._result(request,ResearchPortfolioStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,program in enumerate(request.programs):
            if stopped:
                records.append(ResearchPortfolioProgramRecord(index,program.identity,ResearchPortfolioProgramStatus.SKIPPED,program.program_request,None,None,"Skipped because fail-fast policy stopped the research portfolio."));continue
            try:result=self._executor.run(program.program_request)
            except Exception as exc:
                records.append(ResearchPortfolioProgramRecord(index,program.identity,ResearchPortfolioProgramStatus.PROGRAM_FAILED,program.program_request,None,type(exc).__name__,"Research program invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,ResearchProgramResult):
                records.append(ResearchPortfolioProgramRecord(index,program.identity,ResearchPortfolioProgramStatus.PROGRAM_FAILED,program.program_request,None,"InvalidResearchProgramResult","Research program returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ResearchPortfolioProgramRecord(index,program.identity,ResearchPortfolioProgramStatus.COMPLETED,program.program_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ResearchPortfolioProgramStatus.COMPLETED for x in records)
        if completed==len(records):return ResearchPortfolioStatus.COMPLETED
        if completed:return ResearchPortfolioStatus.PARTIALLY_COMPLETED
        return ResearchPortfolioStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ResearchPortfolioProgramStatus.COMPLETED for x in records);skipped=sum(x.status is ResearchPortfolioProgramStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ResearchPortfolioStatus.DISABLED,ResearchPortfolioStatus.REJECTED) else len(request.programs)
        summary=ResearchPortfolioSummary(total,completed+failed,completed,failed,skipped)
        return ResearchPortfolioResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ResearchPortfolioCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
