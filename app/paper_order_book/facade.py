"""Public delegation facade for the Paper Order Book application."""

from app.paper_order_book.models import (
    PaperOrderBookRequest,
    PaperOrderBookResult,
)
from app.paper_order_book.composition import default_service

_service = default_service()


def execute(
    request: PaperOrderBookRequest,
) -> PaperOrderBookResult:
    return _service.execute(request)


__all__ = ("execute",)
