from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SubscribableMarketDataTransport(Protocol):
    def connect(self) -> None:
        """Connect to the market-data stream."""

    def disconnect(self) -> None:
        """Disconnect from the market-data stream."""

    def subscribe(
        self,
        channels: tuple[str, ...],
    ) -> None:
        """Subscribe to market-data channels."""

    def read_event(self) -> Any | None:
        """Read the next normalized market event."""


@runtime_checkable
class LiveScannerEngine(Protocol):
    def refresh_universe(
        self,
        asset_classes: tuple[Any, ...] = ...,
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        """Refresh eligible symbols and reference data."""

    def consume(self, event: Any) -> Any | None:
        """Process one normalized market event."""

    def snapshot(
        self,
        *,
        limit: int = 25,
    ) -> Any:
        """Return the current scanner snapshot."""
