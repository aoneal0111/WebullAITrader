"""Opt-in desktop crypto PAPER binding to the existing campaign controller."""
from app.asset_modules.controlled_paper_service import ControlledPaperTradingService
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.engine_catalog import EngineId


def validate_crypto_allocation(configuration, composition):
    if not configuration.crypto_shared_allocation_enabled:
        return None
    service = getattr(composition, 'paper_controller_service', None)
    if (configuration.environment.value != 'PAPER' or configuration.live_trading_enabled
            or not configuration.paper_controller_enabled
            or not isinstance(service, ControlledPaperTradingService)):
        raise ValueError('Crypto shared allocation requires the desktop PAPER controller and disabled live trading')
    # Verify the immutable campaign before binding another ledger.
    with service.router.gateway.controller_reconciliation(
            service.router.account_id, campaign_id=service.router.campaign_id):
        pass
    return service


def attach_crypto_allocation(service, paper):
    """Authorize locally; neither the model nor equity bridge controls crypto exits."""
    equity = service.router
    def authorize(request, now):
        if request.engine is not EngineId.CRYPTO or request.account_id != equity.account_id:
            return False
        try:
            return equity.shared_allocation_ready()
        except Exception:
            return False
    router = CryptoAllocationRouter(equity.controller, paper,
        account_id=equity.account_id, authorize=authorize)
    router.reconcile()
    return router
