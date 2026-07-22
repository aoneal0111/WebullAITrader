from typing import Protocol
from app.research_program import ResearchProgramRequest,ResearchProgramResult
class ResearchProgramExecutor(Protocol):
    def run(self,request:ResearchProgramRequest)->ResearchProgramResult:...
