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
from .sec_filings import (
    DEFAULT_RECENT_ROW_LIMIT, MAX_RECENT_ROW_LIMIT, MAX_SUBMISSIONS_BYTES,
    SEC_SOURCE, SEC_SUBMISSIONS_PARSER_VERSION, SecFilingFactNormalizer,
    SecFilingNormalizationDiagnostics, SecFilingNormalizationFailureKind,
    SecFilingNormalizationResult, normalize_accession,
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
    "DEFAULT_RECENT_ROW_LIMIT", "MAX_RECENT_ROW_LIMIT", "MAX_SUBMISSIONS_BYTES",
    "SEC_SOURCE", "SEC_SUBMISSIONS_PARSER_VERSION", "SecFilingFactNormalizer",
    "SecFilingNormalizationDiagnostics", "SecFilingNormalizationFailureKind",
    "SecFilingNormalizationResult", "normalize_accession",
]
