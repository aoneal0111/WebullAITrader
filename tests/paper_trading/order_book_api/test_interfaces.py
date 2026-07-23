import inspect
from typing import get_type_hints

from app.paper_trading.order_book_api import (
    PaperOrderBook,
    PaperOrderBookInterface,
)


def test_existing_book_is_structurally_compatible() -> None:
    assert isinstance(PaperOrderBook(), PaperOrderBookInterface)


def test_interface_contains_only_existing_public_operations() -> None:
    operations = {
        name
        for name, value in vars(PaperOrderBookInterface).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert operations == {
        "submit",
        "update",
        "get",
        "contains",
        "cancel",
        "expire_day_orders",
        "open_orders",
        "open_orders_for_symbol",
        "terminal_orders",
        "history",
    }


def test_interface_signatures_match_existing_book() -> None:
    for name in (
        "submit",
        "update",
        "get",
        "contains",
        "cancel",
        "expire_day_orders",
        "open_orders",
        "open_orders_for_symbol",
        "terminal_orders",
        "history",
    ):
        interface_method = getattr(PaperOrderBookInterface, name)
        implementation_method = getattr(PaperOrderBook, name)
        interface_signature = inspect.signature(interface_method)
        implementation_signature = inspect.signature(implementation_method)

        assert tuple(
            (parameter.name, parameter.kind, parameter.default)
            for parameter in interface_signature.parameters.values()
        ) == tuple(
            (parameter.name, parameter.kind, parameter.default)
            for parameter in implementation_signature.parameters.values()
        )
        assert get_type_hints(interface_method) == get_type_hints(
            implementation_method
        )
