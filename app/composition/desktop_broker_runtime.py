"""Configuration-aware broker composition for the desktop runtime."""

from __future__ import annotations

from collections.abc import Callable

from app.broker_plugins import BrokerRuntime, create_broker_runtime
from app.configuration import OperationalConfiguration, load_configuration
from app.live_execution.account_polling import (
    BrokerAccountSnapshot,
    poll_broker_account,
)
from app.live_execution.broker_factory import (
    build_webull_broker,
    build_webull_market_data_stream,
)
from app.operations.runtime import RuntimeEventSink
from app.services.runtime_drivers.broker import (
    Clock,
    DesktopBrokerRuntimeDriver,
    utc_now,
)


ConfigurationLoader = Callable[[], OperationalConfiguration]
BrokerRuntimeFactory = Callable[..., BrokerRuntime]


def create_configured_desktop_broker_driver(
    *,
    event_sink: RuntimeEventSink,
    account_snapshot_sink: Callable[[BrokerAccountSnapshot], None],
    configuration_loader: ConfigurationLoader = load_configuration,
    broker_runtime_factory: BrokerRuntimeFactory = create_broker_runtime,
    webull_broker_factory: Callable[[object], object] = build_webull_broker,
    webull_market_data_factory: Callable[
        [object], object
    ] = build_webull_market_data_stream,
    account_poller: Callable[..., BrokerAccountSnapshot] = poll_broker_account,
    market_event_observer: Callable[[object], object] | None = None,
    clock: Clock = utc_now,
    source: str = "desktop-broker-runtime",
) -> DesktopBrokerRuntimeDriver:
    """Load configuration and resolve its broker through the plugin registry."""

    if not callable(configuration_loader):
        raise TypeError("configuration_loader must be callable")
    if not callable(broker_runtime_factory):
        raise TypeError("broker_runtime_factory must be callable")
    if not callable(webull_broker_factory):
        raise TypeError("webull_broker_factory must be callable")
    if not callable(webull_market_data_factory):
        raise TypeError("webull_market_data_factory must be callable")
    if not callable(account_snapshot_sink):
        raise TypeError("account_snapshot_sink must be callable")
    if not callable(account_poller):
        raise TypeError("account_poller must be callable")
    if (
        market_event_observer is not None
        and not callable(market_event_observer)
    ):
        raise TypeError(
            "market_event_observer must be callable or None"
        )

    configuration = configuration_loader()
    if not isinstance(configuration, OperationalConfiguration):
        raise TypeError(
            "configuration_loader must return OperationalConfiguration"
        )

    broker_runtime = broker_runtime_factory(
        provider=configuration.broker_provider,
        configuration=configuration,
        webull_broker_factory=webull_broker_factory,
        webull_market_data_factory=webull_market_data_factory,
    )

    return DesktopBrokerRuntimeDriver(
        configuration=configuration,
        broker_runtime=broker_runtime,
        event_sink=event_sink,
        account_snapshot_sink=account_snapshot_sink,
        account_poller=account_poller,
        market_event_observer=market_event_observer,
        clock=clock,
        source=source,
    )


__all__ = [
    "DesktopBrokerRuntimeDriver",
    "create_configured_desktop_broker_driver",
]
