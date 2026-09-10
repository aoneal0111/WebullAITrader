"""Explicit capability detection for optional consolidated NBBO data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NbboSource(StrEnum):
    STREAM = "STREAM"
    REST = "REST"
    SDK = "SDK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NbboCapabilities:
    """What the supplied provider exposes without making a network call."""

    programmatic: bool
    source: NbboSource
    bid_price: bool = False
    bid_size: bool = False
    bid_venue: bool = False
    ask_price: bool = False
    ask_size: bool = False
    ask_venue: bool = False
    timestamp: bool = False
    sequence: bool = False
    quote_condition: bool = False
    reason: str = "NBBO_CAPABILITY_UNAVAILABLE"


_NBBO_METHOD_NAMES = (
    "get_nbbo", "get_consolidated_quote", "get_national_best_bid_offer",
)
_NBBO_STREAM_NAMES = (
    "subscribe_nbbo", "subscribe_consolidated_quote",
)


def detect_nbbo_capabilities(client: object | None) -> NbboCapabilities:
    """Detect explicit NBBO APIs only; ordinary quotes/depth are not NBBO."""

    if client is None:
        return NbboCapabilities(False, NbboSource.UNKNOWN, reason="NBBO_CLIENT_UNAVAILABLE")
    namespaces = tuple(
        value for value in (
            getattr(client, "market_data", None),
            getattr(client, "market_streaming_data", None),
        ) if value is not None
    )
    rest = any(callable(getattr(namespace, name, None)) for namespace in namespaces for name in _NBBO_METHOD_NAMES)
    stream = any(callable(getattr(namespace, name, None)) for namespace in namespaces for name in _NBBO_STREAM_NAMES)
    if rest or stream:
        return NbboCapabilities(
            True, NbboSource.STREAM if stream else NbboSource.REST,
            reason="EXPLICIT_NBBO_API_PRESENT",
        )
    return NbboCapabilities(False, NbboSource.UNKNOWN)


__all__ = ["NbboCapabilities", "NbboSource", "detect_nbbo_capabilities"]
