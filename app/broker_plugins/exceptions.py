"""Errors raised by the broker plugin subsystem."""


class BrokerPluginError(ValueError):
    """Base error for broker plugin configuration and lookup failures."""


class InvalidBrokerPluginError(BrokerPluginError):
    """Raised when an object does not satisfy the broker plugin contract."""


class DuplicateBrokerProviderError(BrokerPluginError):
    """Raised when a provider is registered more than once."""


class UnknownBrokerProviderError(BrokerPluginError):
    """Raised when no plugin is registered for a requested provider."""
