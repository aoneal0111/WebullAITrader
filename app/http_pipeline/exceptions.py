class PipelineError(ValueError):
    """Base error for deterministic HTTP translation."""


class InvalidPipelineRequestError(PipelineError):
    pass


class InvalidPipelineResponseError(PipelineError):
    pass


class SerializationError(PipelineError):
    pass
