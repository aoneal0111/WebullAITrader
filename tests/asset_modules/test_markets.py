from datetime import datetime, UTC, timedelta
from decimal import Decimal as D
from dataclasses import replace
import pytest
from app.assets import AssetType
from app.asset_modules.lifecycle import AssetModules, ModuleAdapter
from app.asset_modules.crypto_paper import CryptoPaper
from app.crypto_research.models import CryptoObservation, CryptoPair


def test_market_budget_and_exposure_are_enforced():
    running = set()
    exposed = set()
    def adapter(asset):
        return ModuleAdapter(lambda: running.add(asset), lambda: (running.discard(asset) or True),
                             lambda: asset in running, lambda: asset in exposed)
    modules = AssetModules({a:adapter(a) for a in (AssetType.EQUITY, AssetType.CRYPTO)}, 1)
    modules.activate(AssetType.EQUITY)
    with pytest.raises(ValueError):
        modules.activate(AssetType.CRYPTO)
    exposed.add(AssetType.EQUITY)
    with pytest.raises(ValueError):
        modules.deactivate(AssetType.EQUITY)
    assert AssetType.EQUITY in running
    with pytest.raises(ValueError):
        modules.activate(AssetType.FUTURES)


def quote():
    return CryptoObservation(CryptoPair('BTC','USD','BTCUSD'), datetime.now(UTC),
                             D('100'), D('99.9'), D('100'), D('10000'))


def buy():
    return dict(id='one',symbol='BTC/USD',action='BUY',notional='200',stop='98',target='108')


def test_crypto_paper_persists_and_exit_costs_are_accounted(tmp_path):
    path = tmp_path/'paper.sqlite3'
    paper = CryptoPaper(path)
    q = quote()
    assert paper.apply(buy(), {'BTC/USD':q})
    assert not paper.apply(buy(), {'BTC/USD':q})
    initial = paper.snapshot()
    assert len(initial['positions']) == 1
    assert D(initial['cash']) < 9800
    paper.close()
    paper = CryptoPaper(path)
    assert paper.snapshot() == initial
    higher = replace(q, bid=D('110'),ask=D('111'))
    paper.protect({'BTC/USD':higher})
    assert paper.snapshot()['positions'] == []
    assert D(paper.snapshot()['realized']) > 0
    assert len(paper.snapshot()['events']) == 2
    paper.close()


@pytest.mark.parametrize('change', [dict(notional='1000'),dict(notional='NaN'),dict(stop='101'),dict(target='101')])
def test_invalid_proposals_never_mutate_account(tmp_path, change):
    paper = CryptoPaper(tmp_path/'paper.sqlite3')
    before = paper.snapshot()
    with pytest.raises(ValueError):
        paper.apply({**buy(),**change},{'BTC/USD':quote()})
    assert paper.snapshot() == before
    paper.close()


def test_recent_receipt_does_not_authorize_stale_quote(tmp_path):
    paper = CryptoPaper(tmp_path/'paper.sqlite3')
    q = replace(quote(), timestamp=datetime.now(UTC)-timedelta(minutes=5))
    with pytest.raises(ValueError):
        paper.apply(buy(),{'BTC/USD':q})
    assert paper.snapshot()['positions'] == []
    paper.close()
