class HTTPRuntimeError(ValueError):pass
class HTTPValidationError(HTTPRuntimeError):pass
class HTTPExecutionError(HTTPRuntimeError):pass
