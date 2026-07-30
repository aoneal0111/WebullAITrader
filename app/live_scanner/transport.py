from __future__ import annotations

from typing import Any


class ReceiveTransportAdapter:
    """
    Adapts a Webull-style client exposing receive() to the
    MarketDataTransport read_event() interface.

    The wrapped client is expected to expose:
        connect()
        disconnect()
        subscribe(channels)
        receive()
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def connect(self) -> None:
        self._client.connect()

    def disconnect(self) -> None:
        self._client.disconnect()

    def subscribe(
        self,
        channels: tuple[str, ...],
    ) -> None:
        self._client.subscribe(channels)

    def read_event(self) -> Any | None:
        return self._client.receive()

    @property
    def client(self) -> Any:
        return self._client

    def set_lifecycle_sink(self, sink) -> None:
        setter = getattr(self._client, "set_lifecycle_sink", None)
        if not callable(setter):
            raise TypeError(
                "market-data client does not support lifecycle events"
            )
        setter(sink)
