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
        "PaperOrderBookRejection",
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
        "create_accept_command",
        "create_apply_fill_command",
        "create_cancel_command",
        "create_expire_command",
        "create_fill",
        "create_observation",
        "create_reject_command",
        "create_request",
        "create_service",
        "create_submit_command",
        "create_update_command",
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
    assert "create_accept_command" in api.__all__
    assert "create_apply_fill_command" in api.__all__
    assert "create_cancel_command" in api.__all__
    assert "create_expire_command" in api.__all__
    assert "create_fill" in api.__all__
    assert "create_observation" in api.__all__
    assert "create_reject_command" in api.__all__
    assert "create_request" in api.__all__
    assert "create_service" in api.__all__
    assert "create_submit_command" in api.__all__
    assert "create_update_command" in api.__all__
    assert "default_service" in api.__all__


def test_star_import_surface_matches_all_exactly() -> None:
    namespace = {}
    exec("from app.paper_order_book import *", {}, namespace)
    assert set(namespace) == set(api.__all__)
