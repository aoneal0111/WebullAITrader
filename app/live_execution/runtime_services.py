"""Production Webull runtime service composition contracts.

This module groups already-constructed Webull services. It deliberately does
not create replacement transports, scanners, universe providers, or reference
data providers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.broker_protocol.protocol import Broker
from app.live_scanner.protocols import LiveScannerEngine
from app.market_data.transport import MarketDataTransport
from app.realtime_scanner.protocols import ReferenceLoader, UniverseSelector


@dataclass(frozen=True, slots=True)
class WebullRuntimeServices:
    """Services owned by one composed Webull runtime.

    Execution is required because it is the currently supported Webull
    capability. Remaining services are optional until their production
    constructors are available and verified.
    """

    execution: Broker
    market_data: MarketDataTransport | None = None
    scanner: LiveScannerEngine | None = None
    universe_provider: UniverseSelector | None = None
    reference_data_provider: ReferenceLoader | None = None

    def __post_init__(self) -> None:
        if self.execution is None:
            raise ValueError("Webull execution service is required")


__all__ = ["WebullRuntimeServices"]
