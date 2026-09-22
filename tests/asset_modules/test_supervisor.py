from dataclasses import replace
from datetime import datetime, UTC, timedelta
from threading import Event
from types import SimpleNamespace
from decimal import Decimal as D

from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.supervisor import CryptoSupervisor
from app.crypto_research.models import CryptoObservation, CryptoPair
from app.crypto_research.runtime import CryptoResearchRuntime


def market():
    return SimpleNamespace(pair=CryptoPair('BTC','USD','BTCUSD'), timestamp=datetime.now(UTC),
                           bid=D('99.9'), ask=D('100'), to_record=lambda: {'symbol':'BTC/USD','bid':'99.9','ask':'100'})


def proposal():
    return dict(symbol='BTC/USD',action='BUY',notional='200',stop='98',target='108')


def test_model_is_off_by_default_and_requests_are_bounded(tmp_path):
    calls = []
    provider = SimpleNamespace(propose=lambda context: calls.append(context) or [proposal()])
    paper = CryptoPaper(tmp_path/'paper.db')
    supervisor = CryptoSupervisor(paper, lambda: [market()], provider)
    supervisor.tick()
    assert not calls
    supervisor.entries_enabled = True
    supervisor.tick()
    supervisor.tick()
    assert len(calls) == 1
    assert len(paper.snapshot()['positions']) == 1
    assert paper.decisions()[0]['outcome'] == 'PAPER FILLED'
    paper.close()


def test_revocation_during_model_call_discards_proposals(tmp_path):
    paper = CryptoPaper(tmp_path/'paper.db')
    supervisor = CryptoSupervisor(paper, lambda: [market()])
    def propose(context):
        supervisor.entries_enabled = False
        return [proposal()]
    supervisor.provider = SimpleNamespace(propose=propose)
    supervisor.entries_enabled = True
    supervisor.tick()
    assert paper.snapshot()['positions'] == []
    paper.close()


def test_quote_is_rechecked_after_model_call(tmp_path):
    paper = CryptoPaper(tmp_path/'paper.db')
    row = market()
    def propose(context):
        row.timestamp -= timedelta(minutes=10)
        return [proposal()]
    supervisor = CryptoSupervisor(paper, lambda:[row], SimpleNamespace(propose=propose))
    supervisor.entries_enabled = True
    supervisor.tick()
    assert paper.snapshot()['positions'] == []
    assert 'REJECTED' in supervisor.status
    paper.close()


def test_protection_continues_while_model_is_blocked(tmp_path):
    paper = CryptoPaper(tmp_path/'paper.db')
    row = market()
    paper.apply({**proposal(),'id':'entry'}, {'BTC/USD':row})
    entered, release = Event(), Event()
    def propose(context):
        entered.set()
        release.wait(5)
        return []
    supervisor = CryptoSupervisor(paper, lambda:[row], SimpleNamespace(propose=propose))
    supervisor.entries_enabled = True
    supervisor.start()
    try:
        assert entered.wait(2)
        row.bid, row.ask = D('110'), D('110.1')
        from time import monotonic, sleep
        deadline = monotonic()+3
        while paper.snapshot()['positions'] and monotonic() < deadline:
            sleep(.02)
        assert not paper.snapshot()['positions']
        assert not release.is_set()
    finally:
        release.set()
        assert supervisor.close()
        paper.close()


def test_research_can_restart_and_persist_new_observations(tmp_path):
    runtime = CryptoResearchRuntime(enabled=True, path=tmp_path/'research.jsonl')
    q = CryptoObservation(CryptoPair('BTC','USD','BTCUSD'), datetime.now(UTC), D('100'), D('99'),D('100'),D('1000'))
    for index in range(2):
        assert runtime.start()
        assert runtime.admit(replace(q, price=D(100+index)))
        assert runtime.close()
    assert runtime.metrics().crypto_episodes_persisted == 2
    assert len((tmp_path/'research.jsonl').read_text().splitlines()) == 2
