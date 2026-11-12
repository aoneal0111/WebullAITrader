from __future__ import annotations
from dataclasses import dataclass
import builtins
import re
from decimal import Decimal

@dataclass(frozen=True)
class WebullTransportError(Exception):
    message: str
    status_code: int | None = None
    retryable: bool = False
    retry_after: Decimal | None = None
    def __str__(self): return self.message
class AuthenticationError(WebullTransportError): pass
class NetworkError(WebullTransportError): pass
class TimeoutError(WebullTransportError): pass
class RateLimitError(WebullTransportError): pass
class ValidationError(WebullTransportError): pass
class SerializationError(WebullTransportError): pass
class BrokerRejectionError(WebullTransportError): pass
class UnknownBrokerError(WebullTransportError): pass


def map_error(exc: Exception) -> WebullTransportError:
    if isinstance(exc, WebullTransportError): return exc
    if isinstance(exc, (ValueError, TypeError)): return ValidationError("transport validation failed")
    if isinstance(exc, builtins.TimeoutError): return TimeoutError("transport timed out", retryable=True)
    if isinstance(exc, (OSError, ConnectionError)): return NetworkError("Webull network operation failed", retryable=True)
    return UnknownBrokerError("unknown Webull transport failure")


def safe_error_identity(error: Exception) -> dict[str, object]:
    """Exception classes, SDK business code and HTTP status only; no raw body."""
    code=str(getattr(error,'error_code','UNKNOWN')).upper()
    if not re.fullmatch(r'[A-Z0-9_.-]{1,80}',code):code='UNKNOWN'
    status=getattr(error,'http_status',None) or getattr(error,'status_code',None)
    if type(status) is not int or not 100<=status<=599:status=None
    return dict(exception_class=type(error).__name__,sdk_error_code=code,http_status=status,
        cause_class=type(error.__cause__).__name__ if error.__cause__ else None)
