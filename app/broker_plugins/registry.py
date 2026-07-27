"""Explicit registry for broker plugins."""

from __future__ import annotations

from collections.abc import Iterator

from app.broker_plugins.exceptions import (
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


class BrokerPluginRegistry:
    """Stores explicitly registered broker plugins by provider name."""

    def __init__(self) -> None:
        self._plugins: dict[str, BrokerPlugin] = {}

    def register(self, plugin: BrokerPlugin) -> None:
        """Register one broker plugin.

        Registration is explicit so startup behavior remains deterministic and
        auditable. The registry never scans directories or imports arbitrary
        modules.
        """

        provider = self._validate_plugin(plugin)

        if provider in self._plugins:
            raise DuplicateBrokerProviderError(
                f"broker provider is already registered: {provider}"
            )

        self._plugins[provider] = plugin

    def unregister(self, provider: str) -> BrokerPlugin:
        """Remove and return a previously registered plugin."""

        normalized = normalize_provider(provider)

        try:
            return self._plugins.pop(normalized)
        except KeyError as exc:
            raise UnknownBrokerProviderError(
                f"unknown broker provider: {normalized}"
            ) from exc

    def get(self, provider: str) -> BrokerPlugin:
        """Return the plugin registered for a provider."""

        normalized = normalize_provider(provider)

        try:
            return self._plugins[normalized]
        except KeyError as exc:
            raise UnknownBrokerProviderError(
                f"unknown broker provider: {normalized}"
            ) from exc

    def create_runtime(
        self,
        provider: str,
        configuration: object,
    ) -> BrokerRuntime:
        """Create and validate a runtime from a registered plugin."""

        normalized = normalize_provider(provider)
        plugin = self.get(normalized)
        runtime = plugin.create_runtime(configuration)

        if not isinstance(runtime, BrokerRuntime):
            raise InvalidBrokerPluginError(
                f"broker plugin {normalized!r} did not return BrokerRuntime"
            )

        if runtime.provider != normalized:
            raise InvalidBrokerPluginError(
                "broker runtime provider does not match its registry provider"
            )

        return runtime

    def capabilities(self, provider: str) -> BrokerCapabilities:
        """Return capability metadata for one provider."""

        return self.get(provider).capabilities

    def providers(self) -> tuple[str, ...]:
        """Return registered provider names in deterministic order."""

        return tuple(sorted(self._plugins))

    def __contains__(self, provider: object) -> bool:
        if not isinstance(provider, str):
            return False

        try:
            normalized = normalize_provider(provider)
        except ValueError:
            return False

        return normalized in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)

    def __iter__(self) -> Iterator[str]:
        return iter(self.providers())

    @staticmethod
    def _validate_plugin(plugin: BrokerPlugin) -> str:
        provider = getattr(plugin, "provider", None)
        capabilities = getattr(plugin, "capabilities", None)
        create_runtime = getattr(plugin, "create_runtime", None)

        try:
            normalized = normalize_provider(provider)
        except ValueError as exc:
            raise InvalidBrokerPluginError(
                "broker plugin must expose a valid provider"
            ) from exc

        if not isinstance(capabilities, BrokerCapabilities):
            raise InvalidBrokerPluginError(
                "broker plugin must expose BrokerCapabilities"
            )

        if capabilities.provider != normalized:
            raise InvalidBrokerPluginError(
                "plugin provider must match its capability provider"
            )

        if not callable(create_runtime):
            raise InvalidBrokerPluginError(
                "broker plugin must implement create_runtime"
            )

        return normalized
