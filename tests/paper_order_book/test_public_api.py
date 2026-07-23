import app.paper_order_book as api
from app.paper_trading import order_book_api as lifecycle


def test_public_exports_are_complete_and_intentional() -> None:
    assert set(api.__all__) == {
        "PaperOrderBookError",
        "PaperOrderBookValidationError",
        "PaperOrderBookSerializationError",
        "PaperOrderBookIdentity",
        "PaperOrderBookObservation",
        "PaperOrderBookCommand",
        "PaperOrderBookRequest",
        "PaperOrderBookPolicy",
        "PaperOrderBookCriteriaResult",
        "PaperOrderBookSummary",
        "PaperOrderBookResult",
        "PaperOrderBookRuntime",
        "PaperOrderBookOrchestrator",
        "PaperOrderBookService",
        "serialize_identity",
        "serialize_snapshot",
        "serialize_command",
        "serialize_policy",
        "serialize_request",
        "serialize_criteria",
        "serialize_summary",
        "serialize_result",
        "validate_request",
        "execute",
    }


def test_application_api_does_not_duplicate_lifecycle_contracts() -> None:
    forbidden = {
        "PaperOrderBook",
        "OrderBookPaperOrder",
        "OrderBookFill",
        "OrderBookOrderStatus",
        "OrderBookOrderSide",
        "OrderBookOrderType",
        "OrderBookTimeInForce",
        "create_order",
        "accept_order",
        "reject_order",
        "cancel_order",
        "expire_order",
        "apply_fill",
    }
    assert forbidden.isdisjoint(api.__all__)
    assert lifecycle.OrderBookPaperOrder is not api.PaperOrderBookCommand
