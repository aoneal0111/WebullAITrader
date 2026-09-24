from __future__ import annotations

from typing import Any
from threading import Thread


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

    def disconnect(self, *, timeout_seconds: float = 5.0) -> None:
        """Disconnect without allowing an SDK stop call to hang Atlas."""
        disconnect = getattr(self._client, "disconnect", None)
        if not callable(disconnect):
            raise TypeError("wrapped market-data client has no disconnect method")
        completed: list[BaseException | None] = []

        def invoke() -> None:
            try:
                disconnect()
            except BaseException as exc:
                completed.append(exc)

        thread = Thread(target=invoke, name="atlas-market-data-disconnect", daemon=True)
        thread.start()
        thread.join(max(0.0, float(timeout_seconds)))
        if thread.is_alive():
            raise TimeoutError("market-data disconnect timed out")
        if completed:
            raise completed[0]

    def halt_callback_ingestion(self) -> bool:
        """Forward terminal callback admission closure to the wrapped client."""
        halt = getattr(self._client, "halt_callback_ingestion", None)
        if not callable(halt):
            return False
        try:
            halt()
        except Exception:
            # Do not replace the original consumer failure during containment.
            return False
        return True

    def subscribe(
        self,
        channels: tuple[str, ...],
    ) -> None:
        self._client.subscribe(channels)

    def read_event(self) -> Any | None:
        return self._client.receive()

    def read_event_nowait(self) -> Any | None:
        receiver = getattr(self._client, "receive_nowait", None)
        if not callable(receiver):
            return None
        return receiver()

    @property
    def client(self) -> Any:
        return self._client

    def memory_metrics(self) -> dict[str, int]:
        provider = getattr(self._client, "memory_metrics", None)
        return {} if not callable(provider) else dict(provider())

    def latency_metrics(self) -> dict[str, float]:
        provider = getattr(self._client, "latency_metrics", None)
        return {} if not callable(provider) else dict(provider())

    def ingestion_metrics(self) -> dict[str, int]:
        provider = getattr(self._client, "ingestion_metrics", None)
        return {} if not callable(provider) else dict(provider())

    def discard_metrics(self) -> dict[str, int]:
        provider = getattr(self._client, "discard_metrics", None)
        return {} if not callable(provider) else dict(provider())

    @property
    def subscription_state(self) -> dict[str, object]:
        return dict(getattr(self._client, "subscription_state", {}))

    def record_generation_outcome(self, outcome: str) -> None:
        recorder = getattr(self._client, "record_generation_outcome", None)
        if callable(recorder):
            recorder(outcome)

    def generation_accounting(self):
        provider = getattr(self._client, "generation_accounting", None)
        return () if not callable(provider) else tuple(provider())

    def set_lifecycle_sink(self, sink) -> None:
        setter = getattr(self._client, "set_lifecycle_sink", None)
        if not callable(setter):
            raise TypeError(
                "market-data client does not support lifecycle events"
            )
        setter(sink)

    @property
    def heartbeat_ok(self) -> bool:
        return bool(getattr(self._client, "heartbeat_ok", False))

    @property
    def subscription_acknowledged(self) -> bool:
        return bool(getattr(self._client, "subscription_acknowledged", False))

    @property
    def reconnect_ready(self) -> bool:
        return bool(getattr(self._client, "reconnect_ready", False))

    @property
    def last_raw_callback_monotonic(self):
        return getattr(self._client, "last_raw_callback_monotonic", None)

    @property
    def last_raw_callback_at(self):
        return getattr(self._client, "last_raw_callback_at", None)

    @property
    def last_normalized_event_monotonic(self):
        return getattr(self._client, "last_normalized_event_monotonic", None)

    @property
    def last_normalized_event_at(self):
        return getattr(self._client, "last_normalized_event_at", None)

    @property
    def generation_metrics(self):
        return getattr(self._client, "generation_metrics", {})
