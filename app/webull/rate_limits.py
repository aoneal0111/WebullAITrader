from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from app.webull.errors import RateLimitError

@dataclass(frozen=True, slots=True)
class RateLimit:
    requests: int
    window_seconds: Decimal

class DeterministicRateLimiter:
    def __init__(self, limit: RateLimit, clock, sleeper):
        if limit.requests <= 0 or limit.window_seconds <= 0: raise ValueError("rate limit is invalid")
        self.limit, self.clock, self.sleeper = limit, clock, sleeper
        self._window_start = clock(); self._used = 0
    def acquire(self, retry_after: Decimal | None = None):
        now = self.clock()
        if now - self._window_start >= self.limit.window_seconds: self._window_start, self._used = now, 0
        if retry_after is not None:
            if retry_after < 0: raise RateLimitError("invalid retry-after")
            self.sleeper(retry_after); self._window_start, self._used = self.clock(), 0
        elif self._used >= self.limit.requests:
            delay = self.limit.window_seconds - (now - self._window_start)
            if delay <= 0: raise RateLimitError("rate limit exhausted")
            self.sleeper(delay); self._window_start, self._used = self.clock(), 0
        self._used += 1
