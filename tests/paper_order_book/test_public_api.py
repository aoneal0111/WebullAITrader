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
        "create_service",
        "default_service",
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


def test_public_exports_are_unique_and_alphabetically_ordered() -> None:
    assert len(api.__all__) == len(set(api.__all__))
    assert api.__all__ == tuple(sorted(api.__all__))


def test_every_public_export_exists_and_is_not_private() -> None:
    assert all(hasattr(api, name) for name in api.__all__)
    assert all(not name.startswith("_") for name in api.__all__)


def test_application_entry_points_are_exported() -> None:
    assert "execute" in api.__all__
    assert "create_service" in api.__all__
    assert "default_service" in api.__all__


def test_star_import_surface_matches_all_exactly() -> None:
    namespace = {}
    exec("from app.paper_order_book import *", {}, namespace)
    assert set(namespace) == set(api.__all__)
