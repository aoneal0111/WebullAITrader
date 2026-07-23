"""Public delegation facade for the Paper Order Book application."""

from app.paper_order_book.models import (
    PaperOrderBookRequest,
    PaperOrderBookResult,
)
from app.paper_order_book.service import PaperOrderBookService

_service = PaperOrderBookService()


def execute(
    request: PaperOrderBookRequest,
) -> PaperOrderBookResult:
    return _service.execute(request)


__all__ = ("execute",)
