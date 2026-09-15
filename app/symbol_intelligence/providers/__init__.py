"""Offline-capable symbol-intelligence provider contracts."""

from .sec_identity import (
    AmbiguousTickerMapError,
    SecIssuerIdentity,
    SecIssuerIdentityResolver,
    SecIssuerResolution,
    SecResolutionStatus,
    SecTickerMap,
    SecTickerMapError,
    normalize_sec_symbol,
    parse_sec_ticker_map,
    sec_cik_path,
    sec_issuer_id,
)
from .sec_transport import (
    SecAcquisitionFailure, SecAcquisitionFailureKind, SecAcquisitionResult,
    SecEdgarEndpointClass, SecEdgarRateLimiter, SecEdgarRequest, SecEdgarResponse,
    SecEdgarTransport, SecTransportMetrics,
)

__all__ = [
    "AmbiguousTickerMapError",
    "SecIssuerIdentity",
    "SecIssuerIdentityResolver",
    "SecIssuerResolution",
    "SecResolutionStatus",
    "SecTickerMap",
    "SecTickerMapError",
    "normalize_sec_symbol",
    "parse_sec_ticker_map",
    "sec_cik_path",
    "sec_issuer_id",
    "SecAcquisitionFailure", "SecAcquisitionFailureKind", "SecAcquisitionResult",
    "SecEdgarEndpointClass", "SecEdgarRateLimiter", "SecEdgarRequest", "SecEdgarResponse",
    "SecEdgarTransport", "SecTransportMetrics",
]
