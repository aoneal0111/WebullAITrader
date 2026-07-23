from typing import Protocol

from app.credentials.models import CredentialRequest, CredentialResponse


class CredentialProvider(Protocol):
    def provide(self, request: CredentialRequest) -> CredentialResponse:
        ...
