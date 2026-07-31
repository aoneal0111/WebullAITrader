from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from app.webull.stream_endpoint import parse_webull_stream_url

@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 3
    initial_backoff_seconds: Decimal = Decimal("0.5")
    multiplier: Decimal = Decimal("2")
    maximum_backoff_seconds: Decimal = Decimal("5")

@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    maximum_attempts: int = 5
    backoff_seconds: Decimal = Decimal("1")

@dataclass(frozen=True, slots=True)
class WebSocketSettings:
    endpoint: str
    heartbeat_timeout_seconds: int = 30

@dataclass(frozen=True, slots=True)
class LoggingConfiguration:
    enabled: bool = True

@dataclass(frozen=True, slots=True)
class WebullConfiguration:
    api_endpoint: str
    account_id: str
    timeout_seconds: Decimal
    retry_policy: RetryPolicy
    reconnect_policy: ReconnectPolicy
    websocket: WebSocketSettings
    logging: LoggingConfiguration = LoggingConfiguration()


def validate_configuration(value: WebullConfiguration) -> WebullConfiguration:
    if not isinstance(value, WebullConfiguration): raise ValueError("WebullConfiguration is required")
    api = urlparse(value.api_endpoint)
    if api.scheme != "https" or not api.netloc: raise ValueError("Webull API endpoint must use HTTPS")
    parse_webull_stream_url(value.websocket.endpoint)
    if not value.account_id.strip(): raise ValueError("account_id is required")
    _positive(value.timeout_seconds, "timeout")
    retry = value.retry_policy
    if retry.maximum_attempts <= 0: raise ValueError("maximum attempts must be positive")
    for item in (retry.initial_backoff_seconds, retry.multiplier, retry.maximum_backoff_seconds): _positive(item, "retry value")
    if value.reconnect_policy.maximum_attempts <= 0: raise ValueError("reconnect attempts must be positive")
    _positive(value.reconnect_policy.backoff_seconds, "reconnect backoff")
    if value.websocket.heartbeat_timeout_seconds <= 0: raise ValueError("heartbeat timeout must be positive")
    return value
def _positive(value, label):
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0: raise ValueError(f"{label} must be a positive Decimal")
