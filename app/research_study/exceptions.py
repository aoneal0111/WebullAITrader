class ResearchStudyError(Exception): pass
class ResearchStudyValidationError(ResearchStudyError): pass
class ResearchStudyDependencyError(ResearchStudyError): pass
class ResearchStudySerializationError(ResearchStudyError): pass
