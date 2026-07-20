from __future__ import annotations
import json
import builtins
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from app.webull.errors import AuthenticationError, BrokerRejectionError, NetworkError, RateLimitError, SerializationError, UnknownBrokerError, ValidationError
from app.webull.retries import execute_with_retry
from decimal import Decimal

@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
class HttpBackend(Protocol):
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds) -> HttpResponse: ...

class UrllibHttpBackend:
    """Production HTTPS backend; authentication/signing headers are supplied by the caller."""
    def send(self, method, url, headers, body, timeout_seconds):
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=float(timeout_seconds)) as response:
                return HttpResponse(response.status, tuple(response.headers.items()), response.read())
        except HTTPError as exc:
            return HttpResponse(exc.code, tuple(exc.headers.items()), exc.read())
        except builtins.TimeoutError as exc:
            from app.webull.errors import TimeoutError as WebullTimeoutError
            raise WebullTimeoutError("Webull HTTP request timed out", retryable=True) from exc
        except URLError as exc:
            raise NetworkError("Webull HTTP network failure", retryable=True) from exc

class WebullHttpClient:
    def __init__(self, endpoint, timeout, retry_policy, backend, auth, limiter, sleeper, logger):
        self.endpoint, self.timeout, self.retry_policy, self.backend = endpoint.rstrip("/"), timeout, retry_policy, backend
        self.auth, self.limiter, self.sleeper, self.logger = auth, limiter, sleeper, logger
    def get(self, path, *, query=None): return self.request("GET", path, query=query)
    def post(self, path, *, payload=None): return self.request("POST", path, payload=payload)
    def put(self, path, *, payload=None): return self.request("PUT", path, payload=payload)
    def delete(self, path, *, payload=None): return self.request("DELETE", path, payload=payload)
    def request(self, method, path, *, query=None, payload=None):
        if method not in ("GET", "POST", "PUT", "DELETE") or not path.startswith("/"): raise ValidationError("invalid HTTP request")
        query_string = urlencode(sorted((query or {}).items()))
        url = self.endpoint + path + (("?" + query_string) if query_string else "")
        try: body = None if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc: raise SerializationError("request payload is not serializable") from exc
        def operation():
            self.limiter.acquire()
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if hasattr(self.auth, "headers"):
                headers.update(self.auth.headers(method, path, tuple(sorted((query or {}).items())), body))
            elif path.startswith("/openapi/"):
                raise AuthenticationError("signed Webull authentication is required")
            else:
                token=self.auth.token();headers["Authorization"]="Bearer "+token.access_token
            response = self.backend.send(method, url, headers, body, self.timeout)
            return self._decode(response)
        self.logger.log("http_request", "started", method=method, path=path)
        try:
            result = execute_with_retry(operation, self.retry_policy, self.sleeper)
            self.logger.log("http_request", "succeeded", method=method, path=path); return result
        except Exception as exc:
            self.logger.log("http_request", "failed", method=method, path=path, error_type=type(exc).__name__); raise

    def _decode(self, response):
        status = response.status_code

        if status == 401 or status == 403:
            raise AuthenticationError(
                "Webull authorization failed",
                status,
            )

        if status == 429:
            retry = dict(
                (key.lower(), value)
                for key, value in response.headers
            ).get("retry-after")

            raise RateLimitError(
                "Webull rate limit exceeded",
                status,
                True,
                Decimal(retry) if retry is not None else None,
            )

        if status in (408, 500, 502, 503, 504):
            raise NetworkError(
                "transient Webull HTTP failure",
                status,
                True,
            )

        if status in (400, 404, 405, 417, 422):
            raise BrokerRejectionError(
                f"Webull rejected the request: "
                f"{response.body.decode('utf-8', errors='replace')}",
                status,
            )

        if not 200 <= status < 300:
            raise UnknownBrokerError(
                "unexpected Webull HTTP response",
                status,
            )

        if not response.body:
            return None

        try:
            return json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise SerializationError(
                "Webull returned malformed JSON"
            ) from exc
