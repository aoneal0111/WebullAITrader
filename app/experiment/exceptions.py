class ExperimentError(Exception): pass
class ExperimentValidationError(ExperimentError): pass
class ExperimentDependencyError(ExperimentError): pass
class ExperimentSerializationError(ExperimentError): pass
