class HTTPXTransportError(ValueError):
    """Base error exposed by the broker-neutral httpx adapter."""


class HTTPXTransportDisabledError(HTTPXTransportError):
    pass


class HTTPXTransportRequestError(HTTPXTransportError):
    pass


class HTTPXTransportTimeoutError(HTTPXTransportError):
    pass


class HTTPXTransportConnectionError(HTTPXTransportError):
    pass


class HTTPXTransportResponseError(HTTPXTransportError):
    pass
