from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

from app.composition.desktop_infrastructure import (
    DesktopScannerInfrastructure,
    create_desktop_scanner_infrastructure,
)
from app.live_scanner.coordinator import LiveScannerCoordinator
from app.live_scanner.transport import ReceiveTransportAdapter
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.scanner_adapter.pipeline import MomentumScannerPipeline


def test_create_desktop_scanner_infrastructure_wires_components() -> None:
    client = Mock()
    universe_service = Mock()
    reference_data_service = Mock()
    scanner_adapter = Mock()
    clock = Mock(return_value=datetime(2026, 7, 24, tzinfo=UTC))

    infrastructure = create_desktop_scanner_infrastructure(
        market_data_client=client,
        universe_service=universe_service,
        reference_data_service=reference_data_service,
        scanner_adapter=scanner_adapter,
        clock=clock,
        default_channels=("quotes", "trades"),
        maximum_events_per_cycle=250,
    )

    assert isinstance(infrastructure, DesktopScannerInfrastructure)
    assert isinstance(infrastructure.transport, ReceiveTransportAdapter)
    assert isinstance(infrastructure.pipeline, MomentumScannerPipeline)
    assert isinstance(infrastructure.engine, RealtimeScannerEngine)
    assert isinstance(infrastructure.coordinator, LiveScannerCoordinator)
    assert infrastructure.transport.client is client


def test_create_desktop_scanner_infrastructure_does_not_connect_client() -> None:
    client = Mock()

    create_desktop_scanner_infrastructure(
        market_data_client=client,
        universe_service=Mock(),
        reference_data_service=Mock(),
        scanner_adapter=Mock(),
    )

    client.connect.assert_not_called()
    client.subscribe.assert_not_called()
    client.receive.assert_not_called()


def test_create_desktop_scanner_infrastructure_subscribes_default_channels() -> None:
    client = Mock()

    infrastructure = create_desktop_scanner_infrastructure(
        market_data_client=client,
        universe_service=Mock(),
        reference_data_service=Mock(),
        scanner_adapter=Mock(),
        default_channels=(" quotes ", "TRADES"),
    )

    infrastructure.coordinator.connect()
    channels = infrastructure.coordinator.subscribe()

    assert channels
    client.connect.assert_called_once_with()
    client.subscribe.assert_called_once_with(channels)
