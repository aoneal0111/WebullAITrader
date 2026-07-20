from __future__ import annotations


class BrokerMutationCapability:
    """Opaque identity token. Instances are issued only to execution-owned adapters."""

    __slots__ = ("__token",)

    def __init__(self, token: object):
        if token is not _ISSUER_TOKEN:
            raise PermissionError("broker mutation capabilities are execution-owned")
        self.__token = object()


_ISSUER_TOKEN = object()


def _issue_broker_mutation_capability() -> BrokerMutationCapability:
    """Internal compatibility hook consumed by the execution adapter only."""
    return BrokerMutationCapability(_ISSUER_TOKEN)
