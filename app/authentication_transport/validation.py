from app.authentication_transport.exceptions import AuthenticationTransportDependencyError
from app.authentication_transport.models import AuthenticationTransportRequest
from app.authentication_transport.policies import AuthenticationTransportPolicy


def validate_dependencies(service, request_factory, pipeline, transport, verifier, policy):
    requirements = (
        (service, ("authenticate", "logout", "state"), "authentication service"),
        (request_factory, ("create",), "request factory"),
        (pipeline, ("prepare", "finalize"), "HTTP request pipeline"),
        (transport, ("send",), "HTTP transport"),
        (verifier, ("verify",), "response verifier"),
    )
    for dependency, operations, name in requirements:
        if any(not callable(getattr(dependency, operation, None)) for operation in operations):
            raise AuthenticationTransportDependencyError(f"{name} has an incompatible interface")
    if not isinstance(policy, AuthenticationTransportPolicy):
        raise AuthenticationTransportDependencyError("policy must be AuthenticationTransportPolicy")
    return True


def validate_request(request):
    if not isinstance(request, AuthenticationTransportRequest):
        raise AuthenticationTransportDependencyError(
            "request must be AuthenticationTransportRequest")
    return request
