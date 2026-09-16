"""Bounded, observation-only symbol intelligence contracts and persistence."""

from .cache import DEFAULT_HOT_SNAPSHOT_MAX, HotCacheMetrics, HotSnapshotCache
from .models import (
    DERIVATION_SCHEMA_VERSION,
    FACT_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    AttentionState,
    CatalystSummary,
    DecayClass,
    Direction,
    EventType,
    IngestResult,
    IntelligenceDerivation,
    IntelligenceEvent,
    SourceAvailability,
    SourceStateSnapshot,
    SecIssuerAcquisitionState,
    SecIssuerAcquisitionStatus,
    SymbolAlias,
    SymbolIdentity,
    SymbolIntelligenceSnapshot,
)
from .repository import (
    DEFAULT_SNAPSHOT_RECOVERY_MAX,
    DEFAULT_STARTUP_RECOVERY_MAX,
    REPOSITORY_SCHEMA_VERSION,
    RepositoryBounds,
    RepositoryMetrics,
    RepositorySchemaError,
    SymbolIntelligenceRepository,
)
from .security import UnsafePayloadError
from .providers.sec_identity import (
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
from .acquisition import (
    AcquisitionFailureKind,
    AcquisitionPriority,
    SecAcquisitionDiagnostics,
    SecAcquisitionMetrics,
    SecSymbolIntelligenceAcquisitionService,
)
from .composition import (
    SymbolIntelligenceComposition,
    SymbolIntelligenceRepositoryComposition,
    create_symbol_intelligence_composition,
    create_symbol_intelligence_repository_composition,
)
from .network_lease import (
    LeaseDiagnostics,
    LeaseMetrics,
    LeaseOutcome,
    LeaseResult,
    LeaseSchemaError,
    SecNetworkOwnershipLease,
    default_lease_path,
)
from .network_ownership_runtime import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_THREAD_NAME,
    SecAcquisitionEligibility,
    SecAcquisitionEligibilityReason,
    SecNetworkOwnershipDiagnostics,
    SecNetworkOwnershipRuntime,
    SecNetworkOwnershipState,
    evaluate_sec_acquisition_eligibility,
)

__all__ = [
    "DEFAULT_HOT_SNAPSHOT_MAX", "DEFAULT_SNAPSHOT_RECOVERY_MAX",
    "DEFAULT_STARTUP_RECOVERY_MAX", "DERIVATION_SCHEMA_VERSION",
    "FACT_SCHEMA_VERSION", "REPOSITORY_SCHEMA_VERSION", "SNAPSHOT_SCHEMA_VERSION",
    "SecIssuerAcquisitionState", "SecIssuerAcquisitionStatus",
    "AttentionState", "CatalystSummary", "DecayClass", "Direction", "EventType",
    "HotCacheMetrics", "HotSnapshotCache", "IngestResult", "IntelligenceDerivation",
    "IntelligenceEvent", "RepositoryBounds", "RepositoryMetrics",
    "RepositorySchemaError", "SourceAvailability", "SourceStateSnapshot",
    "SymbolAlias", "SymbolIdentity", "SymbolIntelligenceRepository",
    "SymbolIntelligenceSnapshot", "UnsafePayloadError",
    "AmbiguousTickerMapError", "SecIssuerIdentity", "SecIssuerResolution",
    "SecIssuerIdentityResolver",
    "SecResolutionStatus", "SecTickerMap", "SecTickerMapError",
    "normalize_sec_symbol", "parse_sec_ticker_map", "sec_cik_path", "sec_issuer_id",
    "AcquisitionFailureKind", "AcquisitionPriority", "SecAcquisitionDiagnostics",
    "SecAcquisitionMetrics", "SecSymbolIntelligenceAcquisitionService",
    "SymbolIntelligenceComposition", "SymbolIntelligenceRepositoryComposition",
    "create_symbol_intelligence_composition", "create_symbol_intelligence_repository_composition",
    "LeaseDiagnostics", "LeaseMetrics", "LeaseOutcome", "LeaseResult",
    "LeaseSchemaError", "SecNetworkOwnershipLease", "default_lease_path",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS", "HEARTBEAT_THREAD_NAME",
    "SecAcquisitionEligibility", "SecAcquisitionEligibilityReason",
    "SecNetworkOwnershipDiagnostics", "SecNetworkOwnershipRuntime",
    "SecNetworkOwnershipState", "evaluate_sec_acquisition_eligibility",
]
