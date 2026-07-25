"""Lifecycle management for composed operational runtime dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from .operational_runtime import OperationalRuntimeComposition


@dataclass(slots=True)
class OperationalRuntimeSession:
    """
    Manage the lifecycle of one composed operational runtime.

    Entering the context does not connect the broker. Call connect() at the
    workflow's existing network-access boundary so durable dependency checks
    can continue to occur before broker connectivity.
    """

    runtime: OperationalRuntimeComposition
    connected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(
            self.runtime,
            OperationalRuntimeComposition,
        ):
            raise TypeError(
                "runtime must be OperationalRuntimeComposition"
            )

    def __enter__(self) -> OperationalRuntimeSession:
        return self

    def connect(self) -> None:
        """Connect the broker and record successful ownership."""

        self.runtime.broker.connect()
        self.connected = True

    def close(self) -> None:
        """Close composed resources in the existing lifecycle order."""

        if self.connected:
            self.runtime.broker.disconnect()
            self.connected = False

        self.runtime.market_store.close()
        self.runtime.emergency_stop.close()
        self.runtime.authorization_registry.close()

        close_journal = getattr(
            self.runtime.execution_journal,
            "close",
            None,
        )
        if callable(close_journal):
            close_journal()

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()


__all__ = ["OperationalRuntimeSession"]
