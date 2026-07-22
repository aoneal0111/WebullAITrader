from app.credentials import CredentialResponse


class FakeCredentialProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def provide(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def response(values=None):
    return CredentialResponse(
        "broker", "order-entry", {"user": "opaque"} if values is None else values)
