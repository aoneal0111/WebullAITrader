from __future__ import annotations
from dataclasses import dataclass
import builtins
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
