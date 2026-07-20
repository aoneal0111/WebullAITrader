from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from app.webull.errors import AuthenticationError

@dataclass(frozen=True, slots=True)
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_timestamp: datetime
    refresh_expires_timestamp: datetime

class CredentialStore(Protocol):
    def load(self) -> OAuthToken | None: ...
    def save(self, token: OAuthToken) -> None: ...
    def clear(self) -> None: ...
class TokenEndpoint(Protocol):
    def exchange_code(self, code: str) -> dict: ...
    def refresh(self, refresh_token: str) -> dict: ...
    def verify(self, access_token: str) -> bool: ...
class AuthenticationHeaders(Protocol):
    def headers(self, method: str, path: str, query: tuple[tuple[str, str], ...], body: bytes | None) -> dict[str, str]: ...

class AuthenticationManager:
    def __init__(self, endpoint: TokenEndpoint, store: CredentialStore, clock): self.endpoint, self.store, self.clock = endpoint, store, clock
    def login(self, authorization_code: str) -> OAuthToken:
        if not authorization_code.strip(): raise AuthenticationError("authorization code is required")
        try: return self._store(self.endpoint.exchange_code(authorization_code))
        except AuthenticationError: raise
        except Exception as exc: raise AuthenticationError("Webull login failed") from exc
    def token(self) -> OAuthToken:
        token = self.store.load()
        if token is None: raise AuthenticationError("authentication is required")
        now = self.clock()
        if now >= token.refresh_expires_timestamp: self.store.clear(); raise AuthenticationError("refresh token expired")
        if now >= token.expires_timestamp:
            try: token = self._store(self.endpoint.refresh(token.refresh_token))
            except AuthenticationError: raise
            except Exception as exc: raise AuthenticationError("Webull token refresh failed") from exc
        return token
    def verify(self) -> bool:
        try: return bool(self.endpoint.verify(self.token().access_token))
        except AuthenticationError: raise
        except Exception as exc: raise AuthenticationError("Webull verification failed") from exc
    def _store(self, value) -> OAuthToken:
        now = self.clock()
        try:
            token = OAuthToken(value["access_token"], value["refresh_token"], now + timedelta(seconds=int(value["expires_in"])), now + timedelta(seconds=int(value["rt_expires_in"])))
        except (KeyError, TypeError, ValueError) as exc: raise AuthenticationError("malformed token response") from exc
        if not token.access_token or not token.refresh_token: raise AuthenticationError("empty token response")
        self.store.save(token); return token
