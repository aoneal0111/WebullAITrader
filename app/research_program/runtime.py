from app.research_study import ResearchStudyResult
from app.research_program.models import *
from app.research_program.validation import validate_dependencies,validate_request
class ResearchProgramRuntime:
    def __init__(self,study_executor):validate_dependencies(study_executor);self._executor=study_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ResearchProgramStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ResearchProgramStatus.REJECTED,(),False,errors)
        if not request.studies:return self._result(request,ResearchProgramStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,study in enumerate(request.studies):
            if stopped:
                records.append(ResearchProgramStudyRecord(index,study.identity,ResearchProgramStudyStatus.SKIPPED,study.study_request,None,None,"Skipped because fail-fast policy stopped the research program."));continue
            try:result=self._executor.run(study.study_request)
            except Exception as exc:
                records.append(ResearchProgramStudyRecord(index,study.identity,ResearchProgramStudyStatus.STUDY_FAILED,study.study_request,None,type(exc).__name__,"Research study invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,ResearchStudyResult):
                records.append(ResearchProgramStudyRecord(index,study.identity,ResearchProgramStudyStatus.STUDY_FAILED,study.study_request,None,"InvalidResearchStudyResult","Research study returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ResearchProgramStudyRecord(index,study.identity,ResearchProgramStudyStatus.COMPLETED,study.study_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ResearchProgramStudyStatus.COMPLETED for x in records)
        if completed==len(records):return ResearchProgramStatus.COMPLETED
        if completed:return ResearchProgramStatus.PARTIALLY_COMPLETED
        return ResearchProgramStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ResearchProgramStudyStatus.COMPLETED for x in records);skipped=sum(x.status is ResearchProgramStudyStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ResearchProgramStatus.DISABLED,ResearchProgramStatus.REJECTED) else len(request.studies)
        summary=ResearchProgramSummary(total,completed+failed,completed,failed,skipped)
        return ResearchProgramResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ResearchProgramCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
