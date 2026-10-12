from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext
import sqlite3

import pytest

from app.asset_modules.crypto_paper import CryptoPaper
from tests.asset_modules.test_markets import buy, quote


def test_entry_identity_and_fill_evidence_survive_restart(tmp_path):
    path=tmp_path/'c.sqlite3';paper=CryptoPaper(path);q=quote()
    assert paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    identity=paper.ledger_id
    evidence=paper.intent_evidence('one')
    assert evidence['state']=='OPEN' and evidence['symbol']=='BTC/USD'
    assert evidence['remaining_quantity']==evidence['bought_quantity']
    assert evidence['entry_cost']>D(200) and evidence['realized_pnl']==0
    dispatch=paper.dispatch_evidence('one')
    assert dispatch['entry_id']=='one' and dispatch['net_pnl'] is None
    paper.close();paper=CryptoPaper(path)
    assert paper.ledger_id==identity and paper.intent_evidence('one')==evidence
    assert paper.dispatch_evidence('one')==dispatch
    assert not paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    assert len(paper.snapshot()['events'])==1
    paper.close()


@pytest.mark.parametrize('changes',[{'notional':'201'},{'stop':'97'},{'target':'109'},
    {'action':'SELL'},{'symbol':'ETH/USD'}])
def test_dispatch_identity_conflict_cannot_rewrite_or_refill(tmp_path,changes):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    before=paper.snapshot();evidence=paper.intent_evidence('one')
    qs={'BTC/USD':q,'ETH/USD':replace(q,pair=replace(q.pair,base_asset='ETH'))}
    with pytest.raises(ValueError,match='identity reused'): paper.apply({**buy(),**changes},qs,now=q.timestamp)
    assert paper.snapshot()==before and paper.intent_evidence('one')==evidence
    paper.close()


def test_equivalent_decimal_terms_and_reason_do_not_change_identity(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    assert not paper.apply({**buy(),'notional':'200.0','reason':'changed explanation'}, {'BTC/USD':q},now=q.timestamp)
    assert paper._fingerprint(buy())==paper._fingerprint({**buy(),'stop':'98.00'})
    with localcontext() as ctx:
        ctx.prec=1
        assert paper._fingerprint(buy())==paper.intent_evidence('one')['fingerprint']
    paper.close()


def test_partials_accumulate_into_one_closed_entry_settlement(tmp_path):
    path=tmp_path/'c.sqlite3';paper=CryptoPaper(path);q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    later=q.timestamp+timedelta(seconds=1)
    q=replace(q,timestamp=later,bid=D(110),ask=D(111))
    first={'id':'partial','symbol':'BTC/USD','action':'SELL','fraction':'.5'}
    paper.apply(first,{'BTC/USD':q},now=later)
    partial=paper.intent_evidence('one')
    assert partial['state']=='OPEN' and partial['settled_at'] is None
    assert 0<partial['remaining_quantity']<partial['bought_quantity'] and partial['realized_pnl']>0
    first_pnl=paper.dispatch_evidence('partial')['net_pnl']
    assert first_pnl==partial['realized_pnl']
    paper.close();paper=CryptoPaper(path)
    later+=timedelta(seconds=1);q=replace(q,timestamp=later)
    final={'id':'final','symbol':'BTC/USD','action':'SELL'}
    paper.apply(final,{'BTC/USD':q},now=later)
    closed=paper.intent_evidence('one')
    assert closed['state']=='CLOSED' and closed['remaining_quantity']==0 and closed['settled_at']==later
    assert closed['realized_pnl']==first_pnl+paper.dispatch_evidence('final')['net_pnl']
    assert closed['realized_pnl']==D(paper.snapshot()['realized'])
    assert not paper.apply(final,{'BTC/USD':q},now=later)
    assert not paper.apply(buy(),{'BTC/USD':q},now=later)
    assert paper.intent_evidence('one')==closed and not paper.snapshot()['positions']
    paper.close()


def test_new_episode_same_pair_does_not_reopen_old_settlement(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    later=q.timestamp+timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(110),ask=D(111))
    paper.protect({'BTC/USD':q},now=later)
    closed=paper.intent_evidence('one')
    later+=timedelta(seconds=1);q=replace(q,timestamp=later,bid=D('99.9'),ask=D(100))
    paper.apply({**buy(),'id':'two'}, {'BTC/USD':q},now=later)
    assert paper.intent_evidence('one')==closed and paper.intent_evidence('two')['state']=='OPEN'
    assert paper.dispatch_evidence('two')['entry_id']=='two'
    paper.close()


def test_journal_failure_rolls_back_intents_and_settlements(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote()
    paper.db.execute("CREATE TRIGGER reject_journal BEFORE INSERT ON journal BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    paper.db.commit()
    with pytest.raises(sqlite3.IntegrityError): paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    assert paper.intent_evidence('one') is None and paper.dispatch_evidence('one') is None
    paper.db.execute('DROP TRIGGER reject_journal');paper.db.commit()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    before=paper.intent_evidence('one')
    paper.db.execute("CREATE TRIGGER reject_journal BEFORE INSERT ON journal BEGIN SELECT RAISE(ABORT,'synthetic failure'); END");paper.db.commit()
    later=q.timestamp+timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(110),ask=D(111))
    with pytest.raises(sqlite3.IntegrityError): paper.apply({'id':'exit','symbol':'BTC/USD','action':'SELL'},{'BTC/USD':q},now=later)
    assert paper.intent_evidence('one')==before and paper.dispatch_evidence('exit') is None
    assert len(paper.snapshot()['positions'])==1
    paper.close()


def test_foreign_database_is_rejected_without_modification(tmp_path):
    path=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE arbitrary (value TEXT)')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='legacy'): CryptoPaper(path)
    assert path.read_bytes()==before
    with sqlite3.connect(path) as db: db.execute('PRAGMA application_id=0x41544f50')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='dedicated'): CryptoPaper(path)
    assert path.read_bytes()==before


def test_legacy_open_position_preserved_without_fabricated_intent(tmp_path):
    path=tmp_path/'legacy.sqlite3';paper=CryptoPaper(path);q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    saved=paper.snapshot();paper.close()
    with sqlite3.connect(path) as db:
        for name in ('crypto_metadata','crypto_intents','crypto_position_intents','crypto_dispatches'): db.execute('DROP TABLE '+name)
        db.execute('PRAGMA application_id=0')
    restored=CryptoPaper(path)
    assert restored.snapshot()==saved and restored.intent_evidence('one') is None
    with pytest.raises(ValueError,match='Legacy dispatch'): restored.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    later=q.timestamp+timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(110),ask=D(111))
    restored.protect({'BTC/USD':q},now=later)
    assert not restored.snapshot()['positions'] and restored.intent_evidence('one') is None
    assert restored.snapshot()['realized']!='0'
    restored.close()


def test_missing_position_link_is_inconsistent_not_flat(tmp_path):
    paper=CryptoPaper(tmp_path/'c.sqlite3');q=quote()
    paper.apply(buy(),{'BTC/USD':q},now=q.timestamp)
    paper.db.execute('DELETE FROM crypto_position_intents');paper.db.commit()
    assert paper.intent_evidence('one')['state']=='INCONSISTENT'
    assert len(paper.snapshot()['positions'])==1
    paper.close()
