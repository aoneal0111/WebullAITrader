"""Public exceptions for Paper Order Book coordination contracts."""


class PaperOrderBookError(Exception):
    """Base error for Paper Order Book application contracts."""


class PaperOrderBookValidationError(PaperOrderBookError):
    """Raised when an application contract is structurally invalid."""


class PaperOrderBookSerializationError(PaperOrderBookError):
    """Raised when an unsupported value is passed to a serializer."""


__all__ = (
    "PaperOrderBookError",
    "PaperOrderBookValidationError",
    "PaperOrderBookSerializationError",
)
