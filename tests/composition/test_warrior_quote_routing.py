"""Offline composition proof for Warrior subscribed quote confirmation."""
from datetime import UTC, datetime
from decimal import Decimal as D
import pytest
import app.composition.desktop as desktop_module
from app.composition import create_desktop_composition
from app.configuration import load_configuration
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload

pytestmark = pytest.mark.usefixtures("historical_intelligence_artifact")


def test_explicit_paper_warrior_and_controller_share_subscribed_quote_confirmation(
    monkeypatch, tmp_path,
):
    from app.equity_overhaul.paper_experiment import create, load, PaperExperiment, ACCOUNT_SIZING_PROFILE
    from app.strategies.warrior_momentum.paper_stream_quote import PaperStreamQuoteSource
    import app.asset_modules.grok_warrior_control as control
    from app.market_data.models import TradePayload
    now = datetime(2026, 11, 16, 18, tzinfo=UTC)
    root = tmp_path/'operation'
    create(root, clock=lambda:now, profile=ACCOUNT_SIZING_PROFILE,
           continuous=True, extended_sessions=True, clock_tolerance=True)
    path, spec = load(root/'experiment.json', clock=lambda:now)
    operation = PaperExperiment(path, spec, clock=lambda:now)
    configuration = load_configuration({
        'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'WARRIOR_FORWARD_PAPER_ENABLED':'true',
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'capture.sqlite3'),
        'ATLAS_PAPER_CONTROLLER_ENABLED':'true',
        'ATLAS_DAILY_RESEARCH_ENABLED':'true',
        'ALLOWED_SYMBOLS':'XYZ',
    })
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    monkeypatch.setattr(desktop_module, 'load_configuration', lambda:configuration)
    import app.equity_overhaul.paper_experiment as experiments
    monkeypatch.setattr(experiments, 'configured', lambda *a:operation)
    monkeypatch.setattr(control, 'bind', lambda *a, **k:None)
    captured = {}
    original_factory = desktop_module.create_desktop_runtime_service
    def runtime_factory(*args, **kwargs):
        captured['observer'] = kwargs['market_event_observer']
        return original_factory(*args, **kwargs)
    monkeypatch.setattr(desktop_module, 'create_desktop_runtime_service', runtime_factory)
    composition = create_desktop_composition(
        paper_persistence_path=tmp_path/'paper.sqlite3', paper_clock=lambda:now)
    try:
        sidecar = composition.warrior_forward_sidecar
        source = sidecar._execution_quote_source
        assert isinstance(source, PaperStreamQuoteSource)
        assert composition.paper_controller_service.quote_source is source
        assert composition.paper_controller_service.scalper_quote_source is source
        # Evidence reaches the source through desktop's actual event observer.
        observer = captured['observer']
        trade = MarketEvent(1, now, 'XYZ', 'webull', MarketEventType.TRADE,
                            TradePayload(D('10'), D('100'), 'fixture'))
        quote = MarketEvent(2, now, 'XYZ', 'webull', MarketEventType.QUOTE,
                            QuotePayload(D('9.99'), D('10.01'), D('100'), D('100')))
        observer(trade)
        observer(quote)
        source.rest = lambda symbol: (_ for _ in ()).throw(AssertionError('REST not needed'))
        confirmed = source('XYZ')
        assert confirmed.bid_timestamp == now
        assert confirmed.bid == D('9.99')
    finally:
        composition.close(timeout_seconds=1.0)
