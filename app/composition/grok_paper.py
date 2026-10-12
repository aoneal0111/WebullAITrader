"""Explicit desktop Grok PAPER entry composition; construction is network-free."""
from datetime import UTC, datetime

from app.asset_modules.grok import configured_client
from app.asset_modules.grok_coordinator import GrokSuite
from app.asset_modules.grok_desktop_producer import DesktopGrokProducer
from app.asset_modules.grok_native_review import NativeGrokReview
from app.configuration.environment import resolve_runtime_environment


def create_grok_paper_suite(configuration, runtime_mode, *, clock=None):
    if not configuration.grok_paper_entries_enabled:
        return None
    if (configuration.environment.value != "PAPER" or runtime_mode.value != "PAPER"
            or configuration.live_trading_enabled or not configuration.paper_controller_enabled
            or not (configuration.warrior_forward_paper_enabled or configuration.quick_scalper_enabled)):
        raise ValueError("Grok entries require PAPER mode, disabled live trading, the PAPER controller and a native entry engine")
    try:
        client = configured_client(resolve_runtime_environment())
    except ValueError:
        raise ValueError("Configure valid XAI_API_KEY and ATLAS_GROK_MODEL for Grok PAPER entries") from None
    if client is None:
        raise ValueError("Configure XAI_API_KEY and ATLAS_GROK_MODEL for Grok PAPER entries")
    return GrokSuite(client, clock=clock or (lambda: datetime.now(UTC)))


def bind_grok_paper_entries(sidecar, suite, service, bridge, *, clock):
    """Each desktop runtime start gets fresh review ownership, sharing one client."""
    def factory():
        review = NativeGrokReview(suite, service, bridge, clock=clock)
        return DesktopGrokProducer(review, permitted=sidecar.grok_native_execution_permitted)
    sidecar.bind_grok_native_producer_factory(factory)
