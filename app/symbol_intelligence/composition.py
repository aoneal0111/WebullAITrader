"""Desktop-lifecycle composition for Symbol Intelligence (Phase 3B).

Production SEC activation is deliberately disabled in this phase.  The
``activate`` seam is explicit and intended for offline tests/shadow wiring.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.symbol_intelligence.acquisition import SecSymbolIntelligenceAcquisitionService
from app.symbol_intelligence.providers.sec_filings import SecFilingFactNormalizer
from app.symbol_intelligence.providers.sec_identity import SecIssuerIdentityResolver
from app.symbol_intelligence.providers.sec_transport import SecEdgarRateLimiter, SecEdgarTransport
from app.symbol_intelligence.repository import SymbolIntelligenceRepository


@dataclass(slots=True)
class SymbolIntelligenceComposition:
    repository: SymbolIntelligenceRepository
    resolver: SecIssuerIdentityResolver
    normalizer: SecFilingFactNormalizer
    limiter: SecEdgarRateLimiter
    transport: SecEdgarTransport
    service: SecSymbolIntelligenceAcquisitionService

    def start(self) -> bool:
        return self.service.start()

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        # The service owns the transport lifecycle and closes it only after its
        # worker has stopped.  Repository operations are per-call connections.
        return self.service.close(timeout_seconds=timeout_seconds)


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
) -> SymbolIntelligenceComposition | None:
    """Build one optional lifecycle owner; never activates production SEC by default."""

    sec_config = getattr(configuration, "symbol_intelligence_sec_edgar", configuration)
    if not bool(getattr(sec_config, "enabled", False)) or not activate:
        return None
    if getattr(configuration, "sec_edgar", None) is not None:
        raise RuntimeError("Symbol Intelligence SEC activation conflicts with legacy SEC downloader")
    environment_name = environment or getattr(getattr(configuration, "environment", None), "value", None) or "TEST"
    path = SymbolIntelligenceRepository.production_path(str(environment_name))
    stack = ExitStack()
    try:
        repository = repository_factory(path)
        resolver = resolver_factory(repository)
        resolver.recover()
        normalizer = normalizer_factory()
        limiter = limiter_factory(float(sec_config.requests_per_second))
        transport = transport_factory(sec_config, limiter=limiter)
        service = service_factory(sec_config, repository, transport, resolver, normalizer=normalizer)
        composition = SymbolIntelligenceComposition(repository, resolver, normalizer, limiter, transport, service)
        if start:
            if not composition.start():
                raise RuntimeError("Symbol Intelligence acquisition worker failed to start")
        stack.pop_all()
        return composition
    except Exception:
        stack.close()
        close = locals().get("transport")
        if close is not None:
            closer = getattr(close, "close", None)
            if callable(closer):
                closer()
        raise


__all__ = ["SymbolIntelligenceComposition", "create_symbol_intelligence_composition"]
