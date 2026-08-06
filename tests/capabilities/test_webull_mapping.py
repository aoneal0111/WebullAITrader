from types import SimpleNamespace

from app.broker_plugins.models import BrokerCapabilities
from app.broker_plugins.webull.capabilities import map_webull_capabilities
from app.capabilities import (
    AssetCapability,
    CapabilityAvailability,
    SessionCapability,
)
from app.webull.market_data_probe import CapabilityStatus, ProbeState
from app.webull.market_data_session import MarketDataSession


def _broker() -> BrokerCapabilities:
    return BrokerCapabilities(
        provider="webull",
        version="test",
        supports_market_data=True,
        supports_stocks=True,
        supports_regular_session=True,
        supports_premarket_session=True,
        supports_after_hours_session=True,
        supports_overnight_session=True,
    )


def _probe(**changes):
    values = {
        "credentials": CapabilityStatus(ProbeState.AVAILABLE),
        "endpoint": CapabilityStatus(ProbeState.AVAILABLE),
        "subscription": CapabilityStatus(ProbeState.AVAILABLE),
        "entitlement": CapabilityStatus(ProbeState.AVAILABLE),
        "current_session": MarketDataSession.REGULAR,
        "scanner_ready": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_webull_adapter_maps_static_assets_and_current_session() -> None:
    result = map_webull_capabilities(_broker(), _probe())

    assert result.availability_for(AssetCapability.STOCKS) is (
        CapabilityAvailability.AVAILABLE
    )
    assert result.availability_for(AssetCapability.OPTIONS) is (
        CapabilityAvailability.BROKER_NOT_SUPPORTED
    )
    assert result.availability_for(SessionCapability.REGULAR) is (
        CapabilityAvailability.AVAILABLE
    )
    assert result.availability_for(SessionCapability.OVERNIGHT) is (
        CapabilityAvailability.MARKET_CLOSED
    )


def test_overnight_entitlement_is_subscription_limitation() -> None:
    unavailable = map_webull_capabilities(
        _broker(),
        _probe(
            current_session=MarketDataSession.OVERNIGHT,
            subscription=CapabilityStatus(ProbeState.NOT_ENTITLED),
            entitlement=CapabilityStatus(ProbeState.NOT_ENTITLED),
            scanner_ready=False,
        ),
    )
    available = map_webull_capabilities(
        _broker(),
        _probe(current_session=MarketDataSession.OVERNIGHT),
    )

    assert unavailable.availability_for(SessionCapability.OVERNIGHT) is (
        CapabilityAvailability.SUBSCRIPTION_REQUIRED
    )
    assert available.availability_for(SessionCapability.OVERNIGHT) is (
        CapabilityAvailability.AVAILABLE
    )


def test_missing_credentials_are_configuration_limitation() -> None:
    result = map_webull_capabilities(
        _broker(),
        _probe(
            credentials=CapabilityStatus(ProbeState.CREDENTIALS_MISSING),
            scanner_ready=False,
        ),
    )

    assert result.availability_for(AssetCapability.STOCKS) is (
        CapabilityAvailability.CONFIGURATION_REQUIRED
    )
    assert result.availability_for(SessionCapability.REGULAR) is (
        CapabilityAvailability.CONFIGURATION_REQUIRED
    )
