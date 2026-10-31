"""Desktop market controls; no crypto authority is inferred from equity mode."""
from decimal import Decimal
from app.assets import AssetType
from app.asset_modules.lifecycle import AssetModules, ModuleAdapter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.supervisor import CryptoSupervisor
from app.asset_modules.grok_coordinator import configured_suite
from app.asset_modules.futures_ledger_view import FuturesLedgerReader
from app.asset_modules.options_ledger_view import OptionsLedgerReader


def create_asset_modules(composition):
    from pathlib import Path
    from app.configuration import load_configuration
    from app.composition.crypto_allocation import validate_crypto_allocation, attach_crypto_allocation
    configuration = load_configuration()
    from app.asset_modules.grok_execution_trial import configured as configured_execution
    execution_lease=configured_execution(configuration)
    shared_service = validate_crypto_allocation(configuration, composition)
    native_observer = configuration.crypto_native_observer_enabled
    if native_observer:
        from app.crypto_research.runtime import CryptoResearchRuntime
        if (shared_service is None or configuration.grok_paper_entries_enabled
                or not isinstance(composition.crypto_research_runtime, CryptoResearchRuntime)):
            raise ValueError('Native crypto observer requires shared PAPER allocation, research runtime and disabled Grok order review')
    execution_path = Path(configuration.execution_database_path)
    paper = CryptoPaper(execution_path.with_name('crypto-paper.sqlite3'))
    try:
        router = None if shared_service is None else attach_crypto_allocation(shared_service, paper)
        if execution_lease is not None:
            if router is None:raise ValueError('Grok execution requires shared crypto admission')
            def capture_ready():
                status=composition.daily_research.journal.status()
                return status['writer_alive'] and status['evidence_complete'] and not status['closed']
            execution_lease.capture_ready=capture_ready
            paper.max_position=Decimal('100');paper.max_loss=Decimal('1')
            paper.maximum_quote_age_seconds=2
            original_authorize=router.authorize
            router.authorize=lambda request,now: original_authorize(request,now) is True and execution_lease.claim_crypto(request,now)
        grok = getattr(composition, "grok_suite", None) or configured_suite()
        if native_observer:
            from app.asset_modules.crypto_native_observer import NativeCryptoObserver
            recorder=getattr(composition,'daily_research',None)
            supervisor = NativeCryptoObserver(paper, composition.crypto_research_runtime,entry_router=router,
                evidence_sink=None if recorder is None else recorder.journal.submit)
            if recorder is not None:
                recorder.crypto_observer=supervisor
        else:
            supervisor = CryptoSupervisor(paper, composition.crypto_research_runtime.latest,
                                          grok.crypto if grok is not None else None, entry_router=router,
                                          maximum_quote_age_seconds=2 if execution_lease else None,
                                          execution_limits=({'maximum_position_dollars':'100','maximum_initial_risk':'1',
                                              'maximum_entry_notional_dollars':'99',
                                              'shared_entry_attempts':50,'crypto_spread_fraction':'0.01'} if execution_lease else None),
                                          history_source=(lambda symbol,now: composition.crypto_research_runtime.completed_bars(symbol,as_of=now,limit=48)) if execution_lease else None,
                                          execution_quote_source=composition.crypto_research_runtime.execution_snapshots if execution_lease else None,
                                          evidence_sink=composition.daily_research.journal.submit if execution_lease else None)
    except Exception:
        paper.close()
        raise
    def equity_exposure():
        state = composition.state_store.snapshot()
        return any(Decimal(row.quantity) != 0 for row in state.positions) or any(
            row.status.upper() not in {'FILLED','CANCELLED','CANCELED','EXPIRED','REJECTED'}
            for row in state.orders
        ) or bool(
            composition.paper_order_book and composition.paper_order_book.open_orders()
        )

    def crypto_active():
        runtime = composition.crypto_research_runtime
        acquisition = composition.crypto_catalyst_acquisition_runtime
        return (runtime is not None and runtime._worker is not None) or (
            acquisition is not None and acquisition._worker is not None
        ) or supervisor.thread is not None

    def start_crypto():
        runtime = composition.crypto_research_runtime
        runtime.enabled = True
        try:
            composition.optional_research.start()
            supervisor.start()
        except Exception:
            supervisor.close()
            composition.optional_research.close()
            raise
        return crypto_active()

    def stop_crypto():
        if not supervisor.close():
            return False
        # Recheck after all in-flight proposals are quiescent.
        if paper.snapshot()['positions']:
            supervisor.start()
            raise ValueError('Paper positions need management; AI proposals disabled, market remains active')
        return composition.optional_research.close()

    adapters = {
        AssetType.EQUITY: ModuleAdapter(
            composition.runtime_service.start,
            composition.runtime_service.close,
            lambda: composition.runtime_service.is_active,
            equity_exposure,
        ),
    }
    if composition.optional_research is not None:
        adapters[AssetType.CRYPTO] = ModuleAdapter(
            start_crypto, stop_crypto,
            crypto_active, lambda: bool(paper.snapshot()['positions']),
        )
    modules = AssetModules(adapters, maximum_active=2)
    modules.crypto_supervisor = supervisor
    modules.grok_suite = grok
    modules.futures_ledger_reader = FuturesLedgerReader(execution_path.with_name('futures-paper.sqlite3'))
    modules.options_ledger_reader = OptionsLedgerReader(execution_path.with_name('options-paper.sqlite3'))
    return modules
