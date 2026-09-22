from decimal import Decimal as D
import pytest
from PySide6.QtWidgets import QApplication
from app.gui.pages.market_analysis import futures_scenario, option_scenario, DerivativeWorkspace
from app.assets import AssetType


def test_futures_multiplier_short_direction_and_tick_validation():
    assert futures_scenario(D('53.60'),D('54'),D('.01'),D('10'),D('2'),D('5'),'Long') == D('790')
    assert futures_scenario(D('54'),D('53.60'),D('.01'),D('10'),D('2'),D('5'),'Short') == D('790')
    with pytest.raises(ValueError):
        futures_scenario(D('54.005'),D('54'),D('.01'),D('10'),D('1'),D('0'),'Long')


def test_long_option_expiry_loss_is_premium_and_fees_with_explicit_multiplier():
    assert option_scenario(D('100'),D('2'),D('90'),D('100'),D('2'),D('1'),'Call') == (D('-402'),D('402'))
    assert option_scenario(D('100'),D('2'),D('90'),D('10'),D('2'),D('1'),'Put') == (D('158'),D('42'))


def test_derivative_gui_computes_scenario_without_a_worker():
    app=QApplication.instance() or QApplication([])
    page=DerivativeWorkspace(AssetType.FUTURES,'Mission Control')
    for key,value in dict(entry='100',exit='101',tick_size='.25',tick_value='1.25',contracts='2',fees='1').items():
        page.inputs[key].setText(value)
    page.calculate.click()
    assert '$8.00' in page.result.text()
    assert page.content.rowCount() == 0
    page.close()


def test_crypto_dashboard_shows_research_and_ages_it_without_marking_stale_positions(tmp_path):
    from datetime import datetime, UTC, timedelta
    from dataclasses import replace
    from app.gui.pages.crypto_paper import CryptoPaperPage
    from app.asset_modules.crypto_paper import CryptoPaper
    from app.asset_modules.supervisor import CryptoSupervisor
    from app.crypto_research.models import CryptoResearchDecision, CryptoResearchRegime
    from app.crypto_research.analysis import calculate_features, score_features
    from tests.crypto_research.test_models_and_analysis import observation
    app=QApplication.instance() or QApplication([])
    now=datetime.now(UTC)
    observed=replace(observation(0,'100','1000'),timestamp=now)
    features=calculate_features((observed,),cutoff=now)
    score,components=score_features(features,())
    row=CryptoResearchDecision('1',observed.pair,now,now,observed.price,observed.bid,observed.ask,
        features.spread,observed.volume,features,(),score,components,1,CryptoResearchRegime.WEEKEND)
    rows=[row]
    paper=CryptoPaper(tmp_path/'crypto.sqlite3'); supervisor=CryptoSupervisor(paper,lambda:rows)
    page=CryptoPaperPage('Mission Control',supervisor)
    try:
        page.show();app.processEvents();page.refresh();page.scanner.selectRow(0)
        assert 'BTC/USD' in page.intelligence.text()
        assert 'FRESH' in page.intelligence.text()
        rows[0]=replace(row,timestamp=now-timedelta(minutes=5))
        page.refresh()
        assert 'STALE / INVALID TIME' in page.intelligence.text()
        assert not supervisor.entries_enabled
    finally:
        page.close();paper.close()
