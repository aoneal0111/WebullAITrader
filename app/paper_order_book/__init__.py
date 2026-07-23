"""Deterministic application contracts around the public paper order book.

This package observes and coordinates caller-owned lifecycle objects. Its
orchestrator delegates mutations and transitions exclusively to the public
lifecycle facade; it does not match orders or calculate fills.
"""

from app.paper_order_book.exceptions import (
    PaperOrderBookError,
    PaperOrderBookSerializationError,
    PaperOrderBookValidationError,
)
from app.paper_order_book.composition import create_service, default_service
from app.paper_order_book.facade import execute
from app.paper_order_book.factories import (
    create_cancel_command,
    create_observation,
    create_request,
    create_submit_command,
    create_update_command,
)
from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookCriteriaResult,
    PaperOrderBookIdentity,
    PaperOrderBookRequest,
    PaperOrderBookResult,
    PaperOrderBookObservation,
    PaperOrderBookSummary,
)
from app.paper_order_book.orchestrator import PaperOrderBookOrchestrator
from app.paper_order_book.policies import PaperOrderBookPolicy
from app.paper_order_book.runtime import PaperOrderBookRuntime
from app.paper_order_book.service import PaperOrderBookService
from app.paper_order_book.serializers import (
    serialize_command,
    serialize_criteria,
    serialize_identity,
    serialize_policy,
    serialize_request,
    serialize_result,
    serialize_snapshot,
    serialize_summary,
)
from app.paper_order_book.validation import validate_request

__all__ = (
    "PaperOrderBookCommand",
    "PaperOrderBookCriteriaResult",
    "PaperOrderBookError",
    "PaperOrderBookIdentity",
    "PaperOrderBookObservation",
    "PaperOrderBookOrchestrator",
    "PaperOrderBookPolicy",
    "PaperOrderBookRequest",
    "PaperOrderBookResult",
    "PaperOrderBookRuntime",
    "PaperOrderBookSerializationError",
    "PaperOrderBookService",
    "PaperOrderBookSummary",
    "PaperOrderBookValidationError",
    "create_cancel_command",
    "create_observation",
    "create_request",
    "create_service",
    "create_submit_command",
    "create_update_command",
    "default_service",
    "execute",
    "serialize_command",
    "serialize_criteria",
    "serialize_identity",
    "serialize_policy",
    "serialize_request",
    "serialize_result",
    "serialize_snapshot",
    "serialize_summary",
    "validate_request",
)
