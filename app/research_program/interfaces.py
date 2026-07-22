from typing import Protocol
from app.research_study import ResearchStudyRequest,ResearchStudyResult
class ResearchStudyExecutor(Protocol):
    def run(self,request:ResearchStudyRequest)->ResearchStudyResult:...
