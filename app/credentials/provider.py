from app.credentials.exceptions import CredentialProviderError
from app.credentials.policies import CredentialPolicy
from app.credentials.validation import validate_request, validate_response


class ValidatingCredentialProvider:
    """Validates an injected provider without discovering or retaining credentials."""

    def __init__(self, provider, policy: CredentialPolicy):
        if not callable(getattr(provider, "provide", None)):
            raise CredentialProviderError("provider must implement provide")
        if not isinstance(policy, CredentialPolicy):
            raise CredentialProviderError("policy must be CredentialPolicy")
        self._provider = provider
        self._policy = policy

    def provide(self, request):
        request = validate_request(request)
        if not self._policy.provider_enabled:
            raise CredentialProviderError("credential provider is disabled")
        try:
            response = self._provider.provide(request)
        except CredentialProviderError:
            raise
        except Exception as exc:
            raise CredentialProviderError("credential provider failed") from exc
        return validate_response(request, response, self._policy)
