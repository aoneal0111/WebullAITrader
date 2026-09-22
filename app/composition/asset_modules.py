"""Desktop market controls; no crypto authority is inferred from equity mode."""
from decimal import Decimal
from app.assets import AssetType
from app.asset_modules.lifecycle import AssetModules, ModuleAdapter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.supervisor import CryptoSupervisor, configured_provider


def create_asset_modules(composition):
    from pathlib import Path
    from app.configuration import load_configuration
    paper = CryptoPaper(Path(load_configuration().execution_database_path).with_name('crypto-paper.sqlite3'))
    supervisor = CryptoSupervisor(paper, composition.crypto_research_runtime.latest, configured_provider())
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
    return modules
