class HTTPClientError(ValueError):pass
class HTTPClientValidationError(HTTPClientError):pass
class HTTPSerializationError(HTTPClientError):pass
class HTTPParsingError(HTTPClientError):pass
class HTTPTransportError(HTTPClientError):pass
