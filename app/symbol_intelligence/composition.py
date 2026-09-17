"""Desktop-lifecycle composition for Symbol Intelligence (Phase 3C-D1).

Production SEC activation is deliberately disabled in this phase.  The
``activate`` seam is explicit and intended for offline tests/shadow wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Sequence
from threading import Lock
from typing import Callable

from app.symbol_intelligence.acquisition import (
    AcquisitionPriority,
    SecSymbolIntelligenceAcquisitionService,
)
from app.symbol_intelligence.models import (
    SecManualTargetDiagnostics,
    SecManualTargetEnqueueResult,
    SecManualTargetEntry,
    SecManualTargetStatus,
    SecStartupTargetDiagnostics,
)
from app.symbol_intelligence.providers.sec_filings import SecFilingFactNormalizer
from app.symbol_intelligence.providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerIdentityResolver,
    SecResolutionStatus,
    normalize_sec_symbol,
)
from app.symbol_intelligence.providers.sec_transport import SecEdgarRateLimiter, SecEdgarTransport
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.symbol_intelligence.network_lease import SecNetworkOwnershipLease, default_lease_path
from app.symbol_intelligence.network_ownership_runtime import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    SecNetworkOwnershipRuntime,
    evaluate_sec_acquisition_eligibility,
)
from app.symbol_intelligence.sec_observability import (
    NOOP_SEC_OBSERVABILITY,
    create_sec_acquisition_observability_sink,
    safe_close,
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
    activation_state: str = "ACTIVE"
    observability: object = field(default=NOOP_SEC_OBSERVABILITY, repr=False)
    _target_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _admitted_target_issuers: set[str] = field(default_factory=set, init=False, repr=False)
    _target_counters: dict[str, int] = field(default_factory=lambda: {
        "requests": 0, "requested_symbols": 0, "accepted": 0,
        "deduplicated": 0, "invalid": 0, "unresolved": 0,
        "ambiguous": 0, "not_ready": 0, "rejected_limit": 0,
        "service_unavailable": 0, "queue_rejected": 0,
    }, init=False, repr=False)
    _startup_target_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _startup_targets: tuple[str, ...] = field(default=(), init=False, repr=False)
    _startup_attempted: bool = field(default=False, init=False, repr=False)
    _startup_consumed: bool = field(default=False, init=False, repr=False)
    _startup_inert: bool = field(default=False, init=False, repr=False)
    _startup_consumed_at: datetime | None = field(default=None, init=False, repr=False)
    _startup_result: SecManualTargetEnqueueResult | None = field(default=None, init=False, repr=False)
    _startup_callback_failure: bool = field(default=False, init=False, repr=False)
    _startup_callback_failure_category: str | None = field(default=None, init=False, repr=False)

    TARGET_LIMIT = 3

    def __post_init__(self) -> None:
        configured = getattr(getattr(self.service, "configuration", None), "manual_targets", ())
        self._startup_targets = tuple(configured or ())

    def register_startup_target_callback(self) -> None:
        """Register the one-shot bridge only when targets were configured."""

        if not self._startup_targets:
            return
        register = getattr(self.service, "set_ticker_map_ready_callback", None)
        if callable(register):
            register(self._consume_startup_targets)

    def _consume_startup_targets(self) -> None:
        with self._startup_target_lock:
            if self._startup_inert or self._startup_attempted or not self._startup_targets:
                return
            self._startup_attempted = True
            targets = self._startup_targets
        try:
            result = self.enqueue_symbols(targets)
        except Exception as error:
            with self._startup_target_lock:
                self._startup_consumed = True
                self._startup_callback_failure = True
                self._startup_callback_failure_category = type(error).__name__[:64]
                self._startup_consumed_at = self._service_time()
            return
        with self._startup_target_lock:
            self._startup_result = result
            self._startup_consumed = True
            self._startup_consumed_at = self._service_time()

    def _service_time(self) -> datetime:
        current_time = getattr(self.service, "current_time", None)
        if callable(current_time):
            return current_time().astimezone(UTC)
        return datetime.now(UTC)

    @property
    def startup_target_diagnostics(self) -> SecStartupTargetDiagnostics:
        with self._startup_target_lock:
            return SecStartupTargetDiagnostics(
                configured_targets=self._startup_targets,
                pending=bool(self._startup_targets)
                and not self._startup_attempted
                and not self._startup_inert,
                attempted=self._startup_attempted,
                consumed=self._startup_consumed,
                consumed_at=self._startup_consumed_at,
                callback_failure=self._startup_callback_failure,
                callback_failure_category=self._startup_callback_failure_category,
                result=self._startup_result,
            )

    def start(self) -> bool:
        if not self.ownership.admission_open:
            self._deactivate_startup_bridge()
            return False
        try:
            started = self.service.start()
        except Exception:
            self._deactivate_startup_bridge()
            raise
        if not started:
            self._deactivate_startup_bridge()
        return started

    def _deactivate_startup_bridge(self) -> None:
        clear = getattr(self.service, "clear_ticker_map_ready_callback", None)
        if callable(clear):
            clear()
        with self._startup_target_lock:
            self._startup_inert = True

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        self._deactivate_startup_bridge()
        try:
            return self.ownership.close(timeout_seconds=timeout_seconds)
        finally:
            safe_close(self.observability)

    def enqueue_symbols(self, symbols: Sequence[str]) -> SecManualTargetEnqueueResult:
        """Admit up to three explicit current-session issuer targets.

        Resolution is local and occurs before the composition session lock;
        only the bounded cap reservation and existing queue admission are
        serialized together.  This method never performs network I/O.
        """
        if isinstance(symbols, str):
            requested_symbols = (symbols,)
        else:
            try:
                requested_symbols = tuple(symbols)
            except TypeError:
                requested_symbols = (symbols,)  # type: ignore[assignment]
        requested = len(requested_symbols)
        with self._target_lock:
            self._target_counters["requests"] += 1
            self._target_counters["requested_symbols"] += requested

        if requested > self.TARGET_LIMIT:
            with self._target_lock:
                self._target_counters["rejected_limit"] += requested
            return SecManualTargetEnqueueResult(
                requested=requested, rejected_limit=requested,
            )

        service = self.service
        service_diagnostics = getattr(service, "diagnostics", None)
        if (
            self.activation_state != "ACTIVE"
            or not bool(getattr(self.ownership, "admission_open", False))
            or service is None
        ):
            with self._target_lock:
                self._target_counters["service_unavailable"] += requested
            return self._uniform_target_result(requested_symbols, SecManualTargetStatus.SERVICE_UNAVAILABLE)
        if not bool(getattr(service_diagnostics, "ticker_map_ready", False)):
            with self._target_lock:
                self._target_counters["not_ready"] += requested
            return self._uniform_target_result(requested_symbols, SecManualTargetStatus.NOT_READY)

        entries: list[SecManualTargetEntry] = []
        resolved: list[tuple[int, SecIssuerIdentity]] = []
        local_ciks: set[int] = set()
        counts = {key: 0 for key in self._target_counters if key not in {"requests", "requested_symbols"}}
        for index, raw_symbol in enumerate(requested_symbols):
            display = str(raw_symbol)[:32]
            try:
                normalized = normalize_sec_symbol(raw_symbol)
            except Exception:
                entries.append(SecManualTargetEntry(display, SecManualTargetStatus.INVALID))
                counts["invalid"] += 1
                continue
            try:
                resolution = self.resolver.resolve_current(normalized)
            except Exception:
                resolution = None
            status = getattr(resolution, "status", None)
            identity = getattr(resolution, "identity", None)
            if status is SecResolutionStatus.AMBIGUOUS or str(status) == SecResolutionStatus.AMBIGUOUS.value:
                entries.append(SecManualTargetEntry(normalized, SecManualTargetStatus.AMBIGUOUS))
                counts["ambiguous"] += 1
            elif status is SecResolutionStatus.RESOLVED and isinstance(identity, SecIssuerIdentity):
                if identity.cik in local_ciks:
                    entries.append(SecManualTargetEntry(normalized, SecManualTargetStatus.DEDUPLICATED))
                    counts["deduplicated"] += 1
                else:
                    local_ciks.add(identity.cik)
                    entries.append(SecManualTargetEntry(normalized, SecManualTargetStatus.ACCEPTED))
                    resolved.append((len(entries) - 1, identity))
            else:
                entries.append(SecManualTargetEntry(normalized, SecManualTargetStatus.UNRESOLVED))
                counts["unresolved"] += 1

        # Reserve the session slot only while performing the bounded queue
        # admission.  enqueue_issuer is local and never performs HTTP.
        for entry_index, identity in resolved:
            with self._target_lock:
                if identity.issuer_id in self._admitted_target_issuers:
                    entries[entry_index] = SecManualTargetEntry(entries[entry_index].symbol, SecManualTargetStatus.DEDUPLICATED)
                    counts["deduplicated"] += 1
                    continue
                if len(self._admitted_target_issuers) >= self.TARGET_LIMIT:
                    entries[entry_index] = SecManualTargetEntry(entries[entry_index].symbol, SecManualTargetStatus.REJECTED_LIMIT)
                    counts["rejected_limit"] += 1
                    continue
                if service.enqueue_issuer(identity, AcquisitionPriority.HIGH_PRIORITY):
                    self._admitted_target_issuers.add(identity.issuer_id)
                    counts["accepted"] += 1
                else:
                    entries[entry_index] = SecManualTargetEntry(entries[entry_index].symbol, SecManualTargetStatus.QUEUE_REJECTED)
                    counts["queue_rejected"] += 1
        with self._target_lock:
            for key, value in counts.items():
                self._target_counters[key] += value
        accepted = counts["accepted"]
        return SecManualTargetEnqueueResult(
            requested=requested, accepted=accepted,
            deduplicated=counts["deduplicated"], invalid=counts["invalid"],
            unresolved=counts["unresolved"], ambiguous=counts["ambiguous"],
            rejected_limit=counts["rejected_limit"], queue_rejected=counts["queue_rejected"],
            entries=tuple(entries),
        )

    def _uniform_target_result(self, symbols: Sequence[str], status: SecManualTargetStatus) -> SecManualTargetEnqueueResult:
        requested = len(symbols)
        entries = tuple(
            SecManualTargetEntry(str(symbol)[:32] or "_", status)
            for symbol in symbols[:self.TARGET_LIMIT]
        )
        values = {status.value.lower(): requested}
        return SecManualTargetEnqueueResult(
            requested=requested,
            not_ready=values.get("not_ready", 0),
            service_unavailable=values.get("service_unavailable", 0),
            entries=entries,
        )

    @property
    def target_diagnostics(self) -> SecManualTargetDiagnostics:
        service_diagnostics = getattr(self.service, "diagnostics", None)
        with self._target_lock:
            values = dict(self._target_counters)
            admitted = len(self._admitted_target_issuers)
        return SecManualTargetDiagnostics(
            ticker_map_ready=bool(getattr(service_diagnostics, "ticker_map_ready", False)),
            ticker_map_last_success_at=getattr(service_diagnostics, "ticker_map_last_success_at", None),
            target_limit=self.TARGET_LIMIT,
            target_unique_issuers_admitted=admitted,
            **values,
        )

    @property
    def diagnostics(self) -> object:
        """Expose only the bounded ownership diagnostics to desktop callers."""
        return self.ownership.diagnostics


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
    repository: SymbolIntelligenceRepository | None = None,
    activation_diagnostics_callback: Callable[[str], None] | None = None,
) -> SymbolIntelligenceComposition | None:
    """Build one optional lifecycle owner; never activates production SEC by default."""

    sec_config = getattr(configuration, "symbol_intelligence_sec_edgar", configuration)
    if not activate:
        if activation_diagnostics_callback:
            activation_diagnostics_callback("DISABLED")
        return None
    eligibility = evaluate_sec_acquisition_eligibility(configuration)
    if not eligibility.eligible:
        if activation_diagnostics_callback:
            activation_diagnostics_callback("INELIGIBLE")
        return None
    environment_name = environment or getattr(getattr(configuration, "environment", None), "value", None) or "TEST"
    path = SymbolIntelligenceRepository.production_path(str(environment_name))
    # Opening the repository is local-only and deliberately precedes lease and
    # network-capable construction.  A caller may provide the exact repository
    # already used by the shadow runtime.
    shared_repository = repository
    if shared_repository is None:
        shared_repository = repository_factory(path)
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
        observability = NOOP_SEC_OBSERVABILITY
        try:
            resolver = resolver_factory(shared_repository)
            resolver.recover()
            normalizer = normalizer_factory()
            limiter = limiter_factory(float(sec_config.requests_per_second))
            transport = transport_factory(sec_config, limiter=limiter)
            service = service_factory(sec_config, shared_repository, transport, resolver, normalizer=normalizer)
            observability = create_sec_acquisition_observability_sink(sec_config)
            set_observability = getattr(service, "set_observability_sink", None)
            if callable(set_observability):
                set_observability(observability)
            holder["service"] = service
            ownership.configure_shutdown(
                close_admission=service.close_admission,
                stop_worker=service.stop,
                close_resource=transport.close,
            )
            composition = SymbolIntelligenceComposition(
                shared_repository, resolver, normalizer, limiter, transport, service, ownership,
                observability=observability,
            )
            composition.register_startup_target_callback()
            return composition
        except Exception:
            safe_close(observability)
            if transport is not None:
                closer = getattr(transport, "close", None)
                if callable(closer):
                    closer()
            raise

    try:
        composition = ownership.authorize_construction(construct)
    except Exception:
        if activation_diagnostics_callback:
            activation_diagnostics_callback("CONSTRUCTION_ERROR")
        raise
    if composition is None:
        if activation_diagnostics_callback:
            activation_diagnostics_callback(ownership.diagnostics.state.value)
        return None
    if start:
        try:
            started = composition.start()
        except Exception:
            if activation_diagnostics_callback:
                activation_diagnostics_callback("START_ERROR")
            composition.close()
            raise
        if not started:
            if activation_diagnostics_callback:
                activation_diagnostics_callback("START_ERROR")
            composition.close()
            raise RuntimeError("Symbol Intelligence acquisition worker failed to start")
    return composition


__all__ = [
    "SymbolIntelligenceComposition",
    "SymbolIntelligenceRepositoryComposition",
    "create_symbol_intelligence_composition",
    "create_symbol_intelligence_repository_composition",
]
