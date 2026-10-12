from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from threading import Barrier

import pytest

from app.asset_modules.crypto_paper import CryptoPaper
from app.crypto_research.models import CryptoPair
from tests.asset_modules.test_markets import buy, quote


@pytest.mark.parametrize('case',['wrong_pair','non_usd','missing','future','naive','stale'])
def test_entry_rejects_wrong_identity_or_time_without_mutation(tmp_path,case):
    paper = CryptoPaper(tmp_path/'c.sqlite3')
    q = quote(); now=q.timestamp
    if case=='wrong_pair': q=replace(q,pair=CryptoPair('ETH','USD','ETHUSD'))
    if case=='non_usd': q=replace(q,pair=CryptoPair('BTC','USDT','BTCUSDT'))
    if case=='missing': q=None
    if case=='future': q=replace(q,timestamp=now+timedelta(seconds=1))
    if case=='naive': now=now.replace(tzinfo=None)
    if case=='stale': now+=timedelta(seconds=91)
    before=paper.snapshot()
    with pytest.raises(ValueError): paper.apply(buy(),{'BTC/USD':q},now=now)
    assert paper.snapshot()==before
    paper.close()


def test_wrong_pair_cannot_trigger_exit(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3')
    q=quote(); paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    later=q.timestamp+timedelta(seconds=1)
    wrong=replace(q,pair=CryptoPair('ETH','USD','ETHUSD'),timestamp=later,bid=D(110),ask=D(111))
    before=paper.snapshot()
    assert paper.protect({'BTC/USD':wrong},now=later)==['BTC/USD: protection quote rejected']
    assert paper.snapshot()==before
    paper.close()


def test_pre_entry_quote_cannot_liquidate_new_position_after_restart(tmp_path):
    path=tmp_path/'c.sqlite3'
    paper=CryptoPaper(path); q=quote(); entered=q.timestamp+timedelta(seconds=1)
    paper.apply(buy(),{'BTC/USD':q},now=entered)
    paper.close(); paper=CryptoPaper(path)
    old=replace(q,bid=D(110),ask=D(111))
    assert paper.protect({'BTC/USD':old},now=entered+timedelta(seconds=1))==['BTC/USD: protection quote rejected']
    assert len(paper.snapshot()['positions'])==1
    fresh=replace(old,timestamp=entered+timedelta(seconds=1))
    assert paper.protect({'BTC/USD':fresh},now=fresh.timestamp)==[]
    assert not paper.snapshot()['positions']
    paper.close()


def test_backdated_fill_does_not_rewind_account(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote();later=q.timestamp+timedelta(seconds=1)
    paper.apply(buy(),{'BTC/USD':q},now=later)
    before=paper.snapshot()
    with pytest.raises(ValueError,match='last recorded fill'):
        paper.apply({'id':'exit','symbol':'BTC/USD','action':'SELL'}, {'BTC/USD':q},now=q.timestamp)
    assert paper.snapshot()==before
    paper.close()


def test_partial_exit_still_requires_post_entry_quote(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote();entered=q.timestamp+timedelta(seconds=1)
    paper.apply(buy(),{'BTC/USD':q},now=entered)
    proposal={'id':'partial','symbol':'BTC/USD','action':'SELL','fraction':'.5'}
    with pytest.raises(ValueError,match='precedes position entry'): paper.apply(proposal,{'BTC/USD':q},now=entered)
    fresh=replace(q,timestamp=entered,bid=D(110),ask=D(111))
    assert paper.apply(proposal,{'BTC/USD':fresh},now=entered)
    assert len(paper.snapshot()['positions'])==1
    assert len(paper.snapshot()['events'])==2
    paper.close()


def test_independent_connections_cannot_both_spend_same_cash(tmp_path):
    path=tmp_path/'c.sqlite3'
    initial=CryptoPaper(path)
    initial.db.execute("UPDATE account SET cash='250'");initial.db.commit();initial.close()
    barrier=Barrier(2)
    q=quote();now=q.timestamp
    def enter(base):
        paper=CryptoPaper(path)
        own=replace(q,pair=CryptoPair(base,'USD',base+'USD'))
        p={**buy(),'id':base,'symbol':base+'/USD'}
        try:
            barrier.wait(timeout=5)
            try:
                return paper.apply(p,{own.pair.canonical_symbol:own},now=now)
            except ValueError:
                return False
        finally: paper.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(enter,('BTC','ETH')))
    assert sorted(results)==[False,True]
    restored=CryptoPaper(path)
    assert len(restored.snapshot()['positions'])==1
    assert D(restored.snapshot()['cash'])>0 and len(restored.snapshot()['events'])==1
    restored.close()


def test_transaction_rolls_back_position_and_balance_after_journal_failure(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote();before=paper.snapshot()
    paper.db.execute("CREATE TRIGGER reject_journal BEFORE INSERT ON journal BEGIN SELECT RAISE(ABORT,'synthetic journal failure'); END")
    paper.db.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError): paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    assert paper.snapshot()==before
    paper.close()
