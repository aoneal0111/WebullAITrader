from unittest.mock import Mock

import pytest

from app.order_cancellation import (
    OrderCancellationRequest,
    OrderCancellationResult,
)
from app.order_placement import (
    OrderPlacementRequest,
    OrderPlacementResult,
)
from app.services import TradingService


def _placement_result() -> OrderPlacementResult:
    return object.__new__(OrderPlacementResult)


def _cancellation_result() -> OrderCancellationResult:
    return object.__new__(OrderCancellationResult)


def test_place_order_delegates_to_placement_runtime() -> None:
    expected = _placement_result()
    placement_runtime = Mock()
    placement_runtime.place_order.return_value = expected

    cancellation_runtime = Mock()
    cancellation_runtime.cancel_order = Mock()

    service = TradingService(
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
    )
    request = object.__new__(OrderPlacementRequest)

    result = service.place_order(request)

    assert result is expected
    placement_runtime.place_order.assert_called_once_with(request)
    cancellation_runtime.cancel_order.assert_not_called()


def test_cancel_order_delegates_to_cancellation_runtime() -> None:
    expected = _cancellation_result()
    placement_runtime = Mock()
    placement_runtime.place_order = Mock()

    cancellation_runtime = Mock()
    cancellation_runtime.cancel_order.return_value = expected

    service = TradingService(
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
    )
    request = object.__new__(OrderCancellationRequest)

    result = service.cancel_order(request)

    assert result is expected
    cancellation_runtime.cancel_order.assert_called_once_with(request)
    placement_runtime.place_order.assert_not_called()


@pytest.mark.parametrize(
    ("placement_runtime", "cancellation_runtime", "message"),
    [
        (None, Mock(cancel_order=Mock()), "placement_runtime must not be None"),
        (Mock(place_order=Mock()), None, "cancellation_runtime must not be None"),
        (
            object(),
            Mock(cancel_order=Mock()),
            "placement_runtime must provide callable place_order()",
        ),
        (
            Mock(place_order=Mock()),
            object(),
            "cancellation_runtime must provide callable cancel_order()",
        ),
    ],
)
def test_constructor_rejects_invalid_dependencies(
    placement_runtime: object,
    cancellation_runtime: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        TradingService(
            placement_runtime=placement_runtime,
            cancellation_runtime=cancellation_runtime,
        )


def test_place_order_rejects_invalid_runtime_result() -> None:
    placement_runtime = Mock()
    placement_runtime.place_order.return_value = object()

    cancellation_runtime = Mock()
    cancellation_runtime.cancel_order = Mock()

    service = TradingService(
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
    )

    with pytest.raises(
        TypeError,
        match=r"placement_runtime\.place_order\(\) must return OrderPlacementResult",
    ):
        service.place_order(object.__new__(OrderPlacementRequest))


def test_cancel_order_rejects_invalid_runtime_result() -> None:
    placement_runtime = Mock()
    placement_runtime.place_order = Mock()

    cancellation_runtime = Mock()
    cancellation_runtime.cancel_order.return_value = object()

    service = TradingService(
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
    )

    with pytest.raises(
        TypeError,
        match=(
            r"cancellation_runtime\.cancel_order\(\) "
            r"must return OrderCancellationResult"
        ),
    ):
        service.cancel_order(object.__new__(OrderCancellationRequest))
