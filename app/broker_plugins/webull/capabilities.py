"""Translate Webull detection results into Atlas capability contracts."""

from __future__ import annotations

from app.broker_plugins.models import BrokerCapabilities
from app.capabilities import (
    AssetCapability,
    CapabilityAvailability,
    CapabilityEntry,
    CapabilitySnapshot,
    SessionCapability,
)
from app.webull.market_data_probe import ProbeState
from app.webull.market_data_session import MarketDataSession


def map_webull_capabilities(
    broker: BrokerCapabilities,
    probe: object,
) -> CapabilitySnapshot:
    """Map detected provider facts without leaking provider terms downstream."""

    credentials = _state(probe, "credentials")
    endpoint = _state(probe, "endpoint")
    configured = credentials is not ProbeState.CREDENTIALS_MISSING
    assets = tuple(
        CapabilityEntry(name, _asset_state(supported, configured, endpoint))
        for name, supported in (
            (AssetCapability.STOCKS, broker.supports_stocks),
            (AssetCapability.OPTIONS, broker.supports_options),
            (AssetCapability.CRYPTO, broker.supports_crypto),
            (AssetCapability.FUTURES, broker.supports_futures),
            (AssetCapability.FOREX, broker.supports_forex),
        )
    )
    current = getattr(probe, "current_session", MarketDataSession.CLOSED)
    sessions = tuple(
        CapabilityEntry(
            name,
            _session_state(
                supported=supported,
                configured=configured,
                current=current,
                expected=expected,
                probe=probe,
            ),
        )
        for name, expected, supported in (
            (
                SessionCapability.REGULAR,
                MarketDataSession.REGULAR,
                broker.supports_regular_session,
            ),
            (
                SessionCapability.PREMARKET,
                MarketDataSession.PREMARKET,
                broker.supports_premarket_session,
            ),
            (
                SessionCapability.AFTER_HOURS,
                MarketDataSession.AFTER_HOURS,
                broker.supports_after_hours_session,
            ),
            (
                SessionCapability.OVERNIGHT,
                MarketDataSession.OVERNIGHT,
                broker.supports_overnight_session,
            ),
        )
    )
    return CapabilitySnapshot(assets=assets, sessions=sessions)


def _asset_state(
    supported: bool,
    configured: bool,
    endpoint: ProbeState | None,
) -> CapabilityAvailability:
    if not supported:
        return CapabilityAvailability.BROKER_NOT_SUPPORTED
    if not configured:
        return CapabilityAvailability.CONFIGURATION_REQUIRED
    if endpoint is ProbeState.AVAILABLE:
        return CapabilityAvailability.AVAILABLE
    if endpoint is ProbeState.UNSUPPORTED:
        return CapabilityAvailability.BROKER_NOT_SUPPORTED
    return CapabilityAvailability.UNKNOWN


def _session_state(
    *,
    supported: bool,
    configured: bool,
    current: object,
    expected: MarketDataSession,
    probe: object,
) -> CapabilityAvailability:
    if not supported:
        return CapabilityAvailability.BROKER_NOT_SUPPORTED
    if not configured:
        return CapabilityAvailability.CONFIGURATION_REQUIRED
    if current is MarketDataSession.CLOSED or current is not expected:
        return CapabilityAvailability.MARKET_CLOSED
    entitlement = _state(probe, "entitlement")
    subscription = _state(probe, "subscription")
    if (
        entitlement is ProbeState.NOT_ENTITLED
        or subscription is ProbeState.NOT_ENTITLED
    ):
        return CapabilityAvailability.SUBSCRIPTION_REQUIRED
    if bool(getattr(probe, "scanner_ready", False)):
        return CapabilityAvailability.AVAILABLE
    if subscription is ProbeState.UNAVAILABLE:
        return CapabilityAvailability.SUBSCRIPTION_REQUIRED
    return CapabilityAvailability.UNKNOWN


def _state(value: object, field_name: str) -> ProbeState | None:
    state = getattr(getattr(value, field_name, None), "state", None)
    return state if isinstance(state, ProbeState) else None


__all__ = ["map_webull_capabilities"]
