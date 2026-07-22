from app.authentication import AuthenticationRequest
from app.credentials import CredentialResponse


class FakeCredentialProvider:
    def __init__(self, response=None, error=None):
        self.response = response or CredentialResponse("broker", "sign-in", {"identity": "opaque"})
        self.error = error
        self.requests = []

    def provide(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


class FakeVerifier:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def verify(self, request, credentials):
        self.calls.append((request, credentials))
        if self.error:
            raise self.error
        return self.result


def request():
    return AuthenticationRequest("broker", "sign-in", ("identity",))
