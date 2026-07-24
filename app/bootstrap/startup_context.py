from dataclasses import dataclass

from app.authorization.registry import AuthorizationRegistry
from app.live_execution.recovery import DurableExecutionJournal
from app.market_data.durable_store import DurableMarketEventStore
from app.operations.emergency_stop import EmergencyStopStore


@dataclass(slots=True)
class StartupContext:
    authorization_registry: AuthorizationRegistry
    execution_journal: DurableExecutionJournal
    market_store: DurableMarketEventStore
    emergency_stop: EmergencyStopStore
    broker: object

    def verify(self) -> None:
        self.authorization_registry.authorizations
        self.execution_journal.pending
        self.market_store.reachable()
        self.emergency_stop.reachable()

    def close(self) -> None:
        disconnect = getattr(self.broker, "disconnect", None)
        if callable(disconnect):
            disconnect()

        for resource in (
            self.market_store,
            self.emergency_stop,
            self.authorization_registry,
            self.execution_journal,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                close()
