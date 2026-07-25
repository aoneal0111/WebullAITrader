from dataclasses import FrozenInstanceError

import pytest

from app.live_execution.runtime_services import WebullRuntimeServices


class ExecutionStub:
    pass


class MarketDataStub:
    pass


class ScannerStub:
    pass


class UniverseStub:
    pass


class ReferenceDataStub:
    pass


def test_webull_runtime_services_groups_existing_services() -> None:
    execution = ExecutionStub()
    market_data = MarketDataStub()
    scanner = ScannerStub()
    universe = UniverseStub()
    reference_data = ReferenceDataStub()

    services = WebullRuntimeServices(
        execution=execution,
        market_data=market_data,
        scanner=scanner,
        universe_provider=universe,
        reference_data_provider=reference_data,
    )

    assert services.execution is execution
    assert services.market_data is market_data
    assert services.scanner is scanner
    assert services.universe_provider is universe
    assert services.reference_data_provider is reference_data


def test_webull_runtime_services_supports_execution_only() -> None:
    execution = ExecutionStub()

    services = WebullRuntimeServices(execution=execution)

    assert services.execution is execution
    assert services.market_data is None
    assert services.scanner is None
    assert services.universe_provider is None
    assert services.reference_data_provider is None


def test_webull_runtime_services_requires_execution() -> None:
    with pytest.raises(
        ValueError,
        match="Webull execution service is required",
    ):
        WebullRuntimeServices(execution=None)  # type: ignore[arg-type]


def test_webull_runtime_services_is_immutable() -> None:
    services = WebullRuntimeServices(execution=ExecutionStub())

    with pytest.raises(FrozenInstanceError):
        services.execution = ExecutionStub()  # type: ignore[misc]
