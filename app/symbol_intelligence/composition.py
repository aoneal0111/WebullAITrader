"""Desktop-lifecycle composition for Symbol Intelligence (Phase 3C-D1).

Production SEC activation is deliberately disabled in this phase.  The
``activate`` seam is explicit and intended for offline tests/shadow wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.symbol_intelligence.acquisition import SecSymbolIntelligenceAcquisitionService
from app.symbol_intelligence.providers.sec_filings import SecFilingFactNormalizer
from app.symbol_intelligence.providers.sec_identity import SecIssuerIdentityResolver
from app.symbol_intelligence.providers.sec_transport import SecEdgarRateLimiter, SecEdgarTransport
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.symbol_intelligence.network_lease import SecNetworkOwnershipLease, default_lease_path
from app.symbol_intelligence.network_ownership_runtime import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    SecNetworkOwnershipRuntime,
    evaluate_sec_acquisition_eligibility,
)


@dataclass(slots=True)
class SymbolIntelligenceComposition:
    repository: SymbolIntelligenceRepository
    resolver: SecIssuerIdentityResolver
    normalizer: SecFilingFactNormalizer
    limiter: SecEdgarRateLimiter
    transport: SecEdgarTransport
    service: SecSymbolIntelligenceAcquisitionService
    ownership: SecNetworkOwnershipRuntime

    def start(self) -> bool:
        if not self.ownership.admission_open:
            return False
        return self.service.start()

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        return self.ownership.close(timeout_seconds=timeout_seconds)


@dataclass(slots=True)
class SymbolIntelligenceRepositoryComposition:
    """Read-only repository owner used by local shadow diagnostics."""

    repository: SymbolIntelligenceRepository

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        # Repository operations use short-lived connections; there is no
        # background service or persistent connection to tear down.
        return True


def create_symbol_intelligence_repository_composition(
    configuration: object,
    *,
    environment: str | None = None,
    repository_factory: Callable[[str | Path], SymbolIntelligenceRepository] = SymbolIntelligenceRepository,
) -> SymbolIntelligenceRepositoryComposition | None:
    """Create the existing SI repository without transport or acquisition."""

    sec_config = getattr(configuration, "symbol_intelligence_sec_edgar", configuration)
    if not bool(getattr(sec_config, "enabled", False)):
        return None
    environment_name = environment or getattr(getattr(configuration, "environment", None), "value", None) or "TEST"
    path = SymbolIntelligenceRepository.production_path(str(environment_name))
    return SymbolIntelligenceRepositoryComposition(repository_factory(path))


def create_symbol_intelligence_composition(
    configuration: object,
    *,
    environment: str | None = None,
    activate: bool = False,
    start: bool = False,
    repository_factory: Callable[[str | Path], SymbolIntelligenceRepository] = SymbolIntelligenceRepository,
    resolver_factory: Callable[[object], SecIssuerIdentityResolver] = SecIssuerIdentityResolver,
    normalizer_factory: Callable[[], SecFilingFactNormalizer] = SecFilingFactNormalizer,
    limiter_factory: Callable[..., SecEdgarRateLimiter] = SecEdgarRateLimiter,
    transport_factory: Callable[..., SecEdgarTransport] = SecEdgarTransport,
    service_factory: Callable[..., SecSymbolIntelligenceAcquisitionService] = SecSymbolIntelligenceAcquisitionService,
    lease_factory: Callable[..., SecNetworkOwnershipLease] = SecNetworkOwnershipLease,
    ownership_runtime_factory: Callable[..., SecNetworkOwnershipRuntime] = SecNetworkOwnershipRuntime,
    lease_path: str | Path | None = None,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> SymbolIntelligenceComposition | None:
    """Build one optional lifecycle owner; never activates production SEC by default."""

    sec_config = getattr(configuration, "symbol_intelligence_sec_edgar", configuration)
    if not activate:
        return None
    eligibility = evaluate_sec_acquisition_eligibility(configuration)
    if not eligibility.eligible:
        return None
    environment_name = environment or getattr(getattr(configuration, "environment", None), "value", None) or "TEST"
    path = SymbolIntelligenceRepository.production_path(str(environment_name))
    holder: dict[str, object] = {}

    def on_lease_lost() -> None:
        service = holder.get("service")
        request_stop = getattr(service, "request_stop", None)
        if callable(request_stop):
            request_stop()

    lease = lease_factory(lease_path or default_lease_path())
    ownership = ownership_runtime_factory(
        lease,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        on_lease_lost=on_lease_lost,
    )

    def construct() -> SymbolIntelligenceComposition:
        transport = None
        try:
            repository = repository_factory(path)
            resolver = resolver_factory(repository)
            resolver.recover()
            normalizer = normalizer_factory()
            limiter = limiter_factory(float(sec_config.requests_per_second))
            transport = transport_factory(sec_config, limiter=limiter)
            service = service_factory(sec_config, repository, transport, resolver, normalizer=normalizer)
            holder["service"] = service
            ownership.configure_shutdown(
                close_admission=service.close_admission,
                stop_worker=service.stop,
                close_resource=transport.close,
            )
            return SymbolIntelligenceComposition(
                repository, resolver, normalizer, limiter, transport, service, ownership,
            )
        except Exception:
            if transport is not None:
                closer = getattr(transport, "close", None)
                if callable(closer):
                    closer()
            raise

    composition = ownership.authorize_construction(construct)
    if composition is None:
        return None
    if start:
        try:
            started = composition.start()
        except Exception:
            composition.close()
            raise
        if not started:
            composition.close()
            raise RuntimeError("Symbol Intelligence acquisition worker failed to start")
    return composition


__all__ = [
    "SymbolIntelligenceComposition",
    "SymbolIntelligenceRepositoryComposition",
    "create_symbol_intelligence_composition",
    "create_symbol_intelligence_repository_composition",
]
