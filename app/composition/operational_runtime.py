"""Composition root for operational runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.authorization.registry import AuthorizationRegistry
from app.configuration.models import OperationalConfiguration
from app.live_execution.recovery import DurableExecutionJournal
from app.market_data.durable_store import DurableMarketEventStore
from app.operations.emergency_stop import EmergencyStopStore


PathFactory = Callable[[Path], Any]
EmergencyStopFactory = Callable[[Path, Callable[[], datetime]], Any]
BrokerFactory = Callable[[OperationalConfiguration], Any]


@dataclass(frozen=True, slots=True)
class OperationalRuntimeComposition:
    """Infrastructure assembled for one operational runtime invocation."""

    configuration: OperationalConfiguration
    authorization_registry: Any
    execution_journal: Any
    market_store: Any
    emergency_stop: Any
    broker: Any


def create_operational_runtime_composition(
    *,
    configuration: OperationalConfiguration,
    clock: Callable[[], datetime],
    broker_factory: BrokerFactory,
    authorization_registry_factory: PathFactory = AuthorizationRegistry,
    execution_journal_factory: PathFactory = DurableExecutionJournal,
    market_store_factory: PathFactory = DurableMarketEventStore,
    emergency_stop_factory: EmergencyStopFactory = EmergencyStopStore,
) -> OperationalRuntimeComposition:
    """
    Assemble durable operational infrastructure and the configured broker.

    This composition root constructs dependencies only. It does not connect the
    broker, reconcile execution state, start observation cycles, or close any
    resources.
    """

    if not isinstance(configuration, OperationalConfiguration):
        raise TypeError(
            "configuration must be OperationalConfiguration"
        )

    for value, name in (
        (clock, "clock"),
        (broker_factory, "broker_factory"),
        (
            authorization_registry_factory,
            "authorization_registry_factory",
        ),
        (
            execution_journal_factory,
            "execution_journal_factory",
        ),
        (
            market_store_factory,
            "market_store_factory",
        ),
        (
            emergency_stop_factory,
            "emergency_stop_factory",
        ),
    ):
        if not callable(value):
            raise TypeError(f"{name} must be callable")

    authorization_registry = authorization_registry_factory(
        configuration.authorization_database_path
    )
    execution_journal = execution_journal_factory(
        configuration.execution_database_path
    )
    market_store = market_store_factory(
        configuration.market_event_database_path
    )
    emergency_stop = emergency_stop_factory(
        configuration.emergency_stop_database_path,
        clock,
    )
    broker = broker_factory(configuration)

    return OperationalRuntimeComposition(
        configuration=configuration,
        authorization_registry=authorization_registry,
        execution_journal=execution_journal,
        market_store=market_store,
        emergency_stop=emergency_stop,
        broker=broker,
    )


__all__ = [
    "OperationalRuntimeComposition",
    "create_operational_runtime_composition",
]
