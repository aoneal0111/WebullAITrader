from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext
import json
import sqlite3

import pytest

from app.asset_modules.crypto_learning_outcomes import crypto_amount, main, read_crypto_outcomes
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.grok_learning import LearningJournal
from tests.asset_modules.test_markets import buy, quote
from tests.asset_modules.test_grok_learning import Client, experiment


def native_ledger(tmp_path, *, closed=True, partials=1, bid='110'):
    path = tmp_path / 'crypto.sqlite3'
    p = CryptoPaper(path)
    q = quote()
    try:
        p.apply(buy(), {'BTC/USD':q}, now=q.timestamp)
        for index in range(partials):
            q = replace(q, timestamp=q.timestamp+timedelta(seconds=1), bid=D(bid), ask=D(bid)+1)
            p.apply(dict(id='partial'+str(index), symbol='BTC/USD', action='SELL', fraction='.5'),
                    {'BTC/USD':q}, now=q.timestamp)
        if closed:
            q = replace(q, timestamp=q.timestamp+timedelta(seconds=1), bid=D(bid), ask=D(bid)+1)
            p.apply(dict(id='final', symbol='BTC/USD', action='SELL'), {'BTC/USD':q}, now=q.timestamp)
        evidence = p.intent_evidence('one')
    finally:
        p.close()
    return path, evidence


@pytest.mark.parametrize('partials,bid', [(0,'110'),(1,'110'),(3,'110'),(2,'95')])
def test_linked_native_partials_make_one_closed_outcome_and_source_is_preserved(tmp_path, partials, bid):
    path, evidence = native_ledger(tmp_path, partials=partials, bid=bid)
    before = path.read_bytes()
    report = read_crypto_outcomes(path)
    assert path.read_bytes() == before and report['excluded'] == {}
    assert len(report['outcomes']) == 1
    o = report['outcomes'][0]
    assert D(o['net_pnl']) == evidence['realized_pnl']
    assert D(o['quantity']) == evidence['bought_quantity']
    assert o['recorded_fees'] == o['policy_version'] == o['strategy_family'] == 'UNKNOWN'
    assert 'NOT_INDEPENDENT_FEE' in o['reconciliation_scope']
    assert 'NOT_EXECUTABLE_FILL' in o['evidence_kind']
    assert (D(o['net_pnl']) > 0) == (bid == '110')
    j = LearningJournal(tmp_path / 'learning.sqlite3')
    with pytest.raises(ValueError): j.ingest_outcomes([o])
    assert j.ingest_proxy_outcomes([o]) == {'inserted':1,'already_present':0}
    assert LearningJournal(j.path).ingest_proxy_outcomes([o]) == {'inserted':0,'already_present':1}
    assert j.outcome_feedback()['groups'] == []
    g = j.proxy_outcome_feedback()['groups'][0]
    assert g['closed_intents'] == 1 and D(g['net_pnl']) == evidence['realized_pnl']
    assert g['cost_scope'] == o['cost_scope'] and j.review_next()['state'] == 'IDLE'


@pytest.mark.parametrize('partials',[0,2])
def test_open_or_partially_closed_entries_are_excluded(tmp_path, partials):
    path, _ = native_ledger(tmp_path, closed=False, partials=partials)
    r = read_crypto_outcomes(path)
    assert r['outcomes'] == [] and r['excluded'] == {'OPEN_INTENTS':1}


@pytest.mark.parametrize('mutation', ['pnl','quantity','entry_cost','closed_at','opened_at',
    'fingerprint','orphan_dispatch','missing_journal','journal_quantity','symbol','model','live_link'])
def test_corruption_refuses_whole_report_without_source_writes(tmp_path, mutation):
    path, _ = native_ledger(tmp_path)
    with sqlite3.connect(path) as db:
        if mutation in {'pnl','quantity','entry_cost','closed_at','opened_at','fingerprint'}:
            column = {'pnl':'realized_pnl'}.get(mutation, mutation)
            value = {'pnl':'100', 'quantity':'100', 'entry_cost':'NaN',
                     'closed_at':'2026-01-01T00:00:00+00:00',
                     'opened_at':'2026-01-01T00:00:00+00:00', 'fingerprint':'f'*64}[mutation]
            db.execute('UPDATE crypto_intents SET '+column+'=?', (value,))
        elif mutation == 'orphan_dispatch':
            db.execute("UPDATE crypto_dispatches SET entry_id='other' WHERE id='final'")
        elif mutation == 'missing_journal':
            db.execute("DELETE FROM journal WHERE id='final'")
        elif mutation == 'live_link':
            db.execute("INSERT INTO crypto_position_intents VALUES ('BTC/USD','one')")
            db.execute("INSERT INTO positions VALUES ('BTC/USD','1','100','98','108')")
        else:
            raw = db.execute("SELECT payload FROM journal WHERE id='final'").fetchone()[0]
            event = json.loads(raw)
            if mutation == 'journal_quantity': event['quantity'] = '1'
            elif mutation == 'symbol': event['symbol'] = 'ETH/USD'
            else: event['model'] = 'ACTUAL_BROKER_FILL'
            db.execute("UPDATE journal SET payload=? WHERE id='final'", (json.dumps(event),))
    before = path.read_bytes()
    with pytest.raises(ValueError): read_crypto_outcomes(path)
    assert path.read_bytes() == before


def test_same_pair_new_episode_remains_separate_from_closed_entry(tmp_path):
    path, _ = native_ledger(tmp_path)
    p = CryptoPaper(path)
    try:
        q = replace(quote(), timestamp=p.intent_evidence('one')['settled_at']+timedelta(seconds=1))
        p.apply({**buy(),'id':'two'}, {'BTC/USD':q}, now=q.timestamp)
    finally: p.close()
    report = read_crypto_outcomes(path)
    assert len(report['outcomes']) == 1 and report['outcomes'][0]['intent_id'] == 'one'
    assert report['excluded'] == {'OPEN_INTENTS':1}


def test_legacy_unlinked_records_are_counted_without_fabricating_outcomes(tmp_path):
    path, _ = native_ledger(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM crypto_intents')
        db.execute('DELETE FROM crypto_dispatches')
    report = read_crypto_outcomes(path)
    assert report['outcomes'] == []
    assert report['excluded'] == {'LEGACY_UNLINKED_JOURNAL_EVENTS':3}


def test_conflict_rolls_back_entire_batch_and_does_not_merge_distinct_ledgers(tmp_path):
    path, _ = native_ledger(tmp_path)
    o = read_crypto_outcomes(path)['outcomes'][0]
    j = LearningJournal(tmp_path / 'learning.sqlite3'); j.ingest_proxy_outcomes([o])
    extra = deepcopy(o); extra['ledger_id'] += '-other'
    conflict = deepcopy(o); conflict['net_pnl'] = '100'
    with pytest.raises(ValueError, match='changed'): j.ingest_proxy_outcomes([extra,conflict])
    assert len(j.proxy_outcome_feedback()['groups']) == 1
    j.ingest_proxy_outcomes([extra])
    assert len(j.proxy_outcome_feedback()['groups']) == 2


def test_high_precision_proxy_totals_do_not_round_to_equity_precision(tmp_path):
    path, _ = native_ledger(tmp_path)
    o = read_crypto_outcomes(path)['outcomes'][0]
    o['net_pnl'] = '0.'+'1'*90
    j = LearningJournal(tmp_path / 'learning.sqlite3'); j.ingest_proxy_outcomes([o])
    assert j.proxy_outcome_feedback()['groups'][0]['net_pnl'] == o['net_pnl']


def test_review_receives_separate_frozen_crypto_feedback(tmp_path):
    path, _ = native_ledger(tmp_path)
    o = read_crypto_outcomes(path)['outcomes'][0]
    j = LearningJournal(tmp_path / 'learning.sqlite3'); j.ingest_proxy_outcomes([o])
    j.ingest(experiment()); client = Client()
    review = j.review('trial-v1',client=client)
    assert client.calls[0][1]['quote_simulation_feedback']['groups'][0]['engine'] == 'CRYPTO'
    assert client.calls[0][1]['observational_outcome_feedback']['groups'] == []
    extra = deepcopy(o); extra['intent_id'] = 'second'
    j.ingest_proxy_outcomes([extra])
    assert j.review('trial-v1')['proxy_outcome_feedback'] == review['proxy_outcome_feedback']
    assert len(client.calls) == 1 and review['promotion'] == 'NONE'


@pytest.mark.parametrize('bad',['NaN','Infinity','1e99','1e-101','1'*129])
def test_crypto_amount_bounds_are_independent_of_ambient_precision(bad):
    with localcontext() as ctx:
        ctx.prec = 1
        with pytest.raises(ValueError): crypto_amount(bad)
        assert crypto_amount('0.123456789012345678901234567890') == D('0.123456789012345678901234567890')


def test_cli_is_offline_and_missing_or_same_path_preserves_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('XAI_API_KEY','do-not-call')
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError): read_crypto_outcomes(missing)
    assert not missing.exists()
    path, _ = native_ledger(tmp_path)
    before = path.read_bytes()
    with pytest.raises(SystemExit): main(['--ledger',str(path),'--journal',str(path)])
    capsys.readouterr()
    assert main(['--ledger',str(path),'--journal',str(tmp_path/'learning.sqlite3')]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['network_requests'] == report['orders'] == 0 and report['promotion'] == 'NONE'
    assert path.read_bytes() == before
