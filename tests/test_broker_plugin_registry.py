from __future__ import annotations

import pytest

from app.broker_plugins import (
    BrokerCapabilities,
    BrokerPluginRegistry,
    BrokerRuntime,
    DuplicateBrokerProviderError,
    InvalidBrokerPluginError,
    UnknownBrokerProviderError,
    normalize_provider,
)


class FakePlugin:
    def __init__(
        self,
        provider: str = "example",
        *,
        runtime_provider: str | None = None,
    ) -> None:
        self.provider = provider
        self.capabilities = BrokerCapabilities(
            provider=provider,
            version="1.0",
            supports_account_data=True,
        )
        self.runtime_provider = runtime_provider

    def create_runtime(self, configuration: object) -> BrokerRuntime:
        del configuration

        return BrokerRuntime(
            provider=self.runtime_provider or self.provider,
            capabilities=self.capabilities,
        )


def test_normalize_provider_is_case_insensitive_and_trims() -> None:
    assert normalize_provider("  Webull  ") == "webull"


@pytest.mark.parametrize(
    "provider",
    [
        "",
        "   ",
        "webull official",
        "webull/sdk",
        "webull.sdk",
    ],
)
def test_normalize_provider_rejects_invalid_values(provider: str) -> None:
    with pytest.raises(ValueError):
        normalize_provider(provider)


def test_registry_registers_and_resolves_plugin() -> None:
    registry = BrokerPluginRegistry()
    plugin = FakePlugin("Webull")

    registry.register(plugin)

    assert registry.get("webull") is plugin
    assert registry.get(" WEBULL ") is plugin
    assert registry.providers() == ("webull",)
    assert "WEBULL" in registry
    assert len(registry) == 1


def test_registry_rejects_duplicate_normalized_provider() -> None:
    registry = BrokerPluginRegistry()
    registry.register(FakePlugin("Webull"))

    with pytest.raises(
        DuplicateBrokerProviderError,
        match="already registered",
    ):
        registry.register(FakePlugin("WEBULL"))


def test_registry_rejects_unknown_provider() -> None:
    registry = BrokerPluginRegistry()

    with pytest.raises(
        UnknownBrokerProviderError,
        match="unknown broker provider",
    ):
        registry.get("missing")


def test_registry_creates_broker_runtime() -> None:
    registry = BrokerPluginRegistry()
    registry.register(FakePlugin("example"))

    configuration = object()
    runtime = registry.create_runtime("EXAMPLE", configuration)

    assert runtime.provider == "example"
    assert runtime.capabilities.provider == "example"


def test_registry_rejects_runtime_for_different_provider() -> None:
    registry = BrokerPluginRegistry()
    registry.register(
        FakePlugin(
            "example",
            runtime_provider="different",
        )
    )

    with pytest.raises(ValueError, match="capability provider"):
        registry.create_runtime("example", object())


def test_registry_rejects_malformed_plugin() -> None:
    registry = BrokerPluginRegistry()

    class MalformedPlugin:
        provider = "broken"
        capabilities = object()

        def create_runtime(self, configuration: object) -> BrokerRuntime:
            raise AssertionError(configuration)

    with pytest.raises(
        InvalidBrokerPluginError,
        match="BrokerCapabilities",
    ):
        registry.register(MalformedPlugin())


def test_registry_rejects_non_runtime_result() -> None:
    registry = BrokerPluginRegistry()

    class InvalidRuntimePlugin:
        provider = "broken"
        capabilities = BrokerCapabilities(
            provider="broken",
            version="1.0",
        )

        def create_runtime(self, configuration: object) -> object:
            return configuration

    registry.register(InvalidRuntimePlugin())

    with pytest.raises(
        InvalidBrokerPluginError,
        match="did not return BrokerRuntime",
    ):
        registry.create_runtime("broken", object())


def test_unregister_returns_plugin_and_removes_provider() -> None:
    registry = BrokerPluginRegistry()
    plugin = FakePlugin()
    registry.register(plugin)

    removed = registry.unregister("EXAMPLE")

    assert removed is plugin
    assert registry.providers() == ()
