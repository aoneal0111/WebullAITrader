"""Broker-neutral plugin registration and runtime composition."""

from app.broker_plugins.builtins import (
    create_builtin_broker_registry,
)
from app.broker_plugins.exceptions import (
    BrokerPluginError,
    DuplicateBrokerProviderError,
    InvalidBrokerPluginError,
    UnknownBrokerProviderError,
)
from app.broker_plugins.models import (
    BrokerCapabilities,
    BrokerRuntime,
    normalize_provider,
)
from app.broker_plugins.protocols import BrokerPlugin
from app.broker_plugins.registry import BrokerPluginRegistry

__all__ = [
    "BrokerCapabilities",
    "BrokerPlugin",
    "BrokerPluginError",
    "BrokerPluginRegistry",
    "BrokerRuntime",
    "DuplicateBrokerProviderError",
    "InvalidBrokerPluginError",
    "UnknownBrokerProviderError",
    "create_builtin_broker_registry",
    "normalize_provider",
]
