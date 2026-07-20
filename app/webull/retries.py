from __future__ import annotations
from decimal import Decimal
from app.webull.configuration import RetryPolicy
from app.webull.errors import RateLimitError, WebullTransportError, map_error

def execute_with_retry(operation, policy: RetryPolicy, sleeper) :
    for attempt in range(policy.maximum_attempts):
        try: return operation()
        except Exception as exc:
            error = map_error(exc)
            if not error.retryable or attempt + 1 >= policy.maximum_attempts: raise error
            delay = (getattr(error, "retry_after", None) if isinstance(error, RateLimitError) else None)
            delay = delay if delay is not None else min(policy.initial_backoff_seconds * (policy.multiplier ** attempt), policy.maximum_backoff_seconds)
            sleeper(delay)
    raise WebullTransportError("retry exhaustion")
