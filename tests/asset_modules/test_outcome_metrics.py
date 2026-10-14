from copy import deepcopy
from decimal import Decimal as D, localcontext
import json
import sqlite3

import pytest

from app.asset_modules.outcome_metrics import outcome_metrics
from app.asset_modules.learning_performance_check import main, read_performance
from app.asset_modules.grok_learning import LearningJournal
from tests.asset_modules.test_learning_outcomes import ledger, order
from app.asset_modules.learning_outcomes import read_equity_outcomes
from tests.asset_modules.test_crypto_learning_outcomes import native_ledger
from app.asset_modules.crypto_learning_outcomes import read_crypto_outcomes
from tests.asset_modules.test_grok_learning import Client, experiment


def test_high_win_rate_can_have_negative_net_expectancy_and_profit_factor():
    pnls = [D(5)]*17+[D(-40)]*3
    r = outcome_metrics(pnls)
    assert r['win_rate'] == '0.85' and r['net_pnl'] == '-35'
    assert r['mean_net_pnl'] == '-1.75'
    assert r['average_win_pnl'] == '5' and r['average_loss_pnl'] == '-40'
    assert D(r['profit_factor']) < 1 and r['profit_factor_state'] == 'DEFINED'
    assert r['gross_winning_pnl'] == '85' and r['gross_losing_pnl'] == '-120'
    assert r['largest_loss_pnl'] == '-40'


@pytest.mark.parametrize('pnls,win_rate,mean,state,factor',[
    ([],None,None,'NO_CLOSED_OUTCOMES',None),
    (['1','2'],'1','1.5','NO_LOSSES',None),
    (['-1','-3'],'0','-2','DEFINED','0'),
    (['0','0'],'0','0','NO_LOSSES',None),
    (['4','-2','0'],'0.3333333333333333333333333333','0.6666666666666666666666666667','DEFINED','2'),
])
def test_empty_winner_loser_and_breakeven_denominators(pnls,win_rate,mean,state,factor):
    r = outcome_metrics([D(x) for x in pnls])
    assert (r['win_rate'],r['mean_net_pnl'],r['profit_factor_state'],r['profit_factor']) == (win_rate,mean,state,factor)
    json.dumps(r,allow_nan=False)
    if not any(D(x)>0 for x in pnls): assert r['average_win_pnl'] is None
    if not any(D(x)<0 for x in pnls): assert r['average_loss_pnl'] is None


def test_precision_is_independent_of_ambient_context_and_totals_preserve_crypto_digits():
    value = D('0.'+'1'*90)
    expected = outcome_metrics([value,value,D('-0.01')])
    with localcontext() as ctx:
        ctx.prec = 1
        assert outcome_metrics([value,value,D('-0.01')]) == expected
    assert outcome_metrics([value])['net_pnl'] == str(value)
    assert expected['largest_loss_share'] == '1'


@pytest.mark.parametrize('bad',[D('NaN'),D('Infinity'),D('1e99'),D('1e-101'),1,True])
def test_non_decimal_and_unbounded_inputs_refused(bad):
    with pytest.raises(ValueError): outcome_metrics([bad])


def journal_with_outcomes(tmp_path):
    source = ledger(tmp_path,[order('BUY','10','100'),order('SELL','10','102')])
    equity = read_equity_outcomes(source)['outcomes']
    crypto_path,_ = native_ledger(tmp_path)
    proxies = read_crypto_outcomes(crypto_path)['outcomes']
    j = LearningJournal(tmp_path/'learning.sqlite3')
    j.ingest_outcomes(equity); j.ingest_proxy_outcomes(proxies)
    return j,equity,proxies


def test_read_only_report_groups_scopes_and_reimports_preserve_immutable_outcomes(tmp_path):
    j,equity,proxies = journal_with_outcomes(tmp_path)
    before = j.path.read_bytes()
    report = read_performance(j.path)
    assert j.path.read_bytes() == before
    e = report['equity']['groups'][0]; p = report['simulations']['groups'][0]
    assert e['net_pnl'] == e['mean_net_pnl'] == '19.00'
    assert e['win_rate'] == '1' and e['policy_version'] == 'UNKNOWN'
    assert p['engine'] == 'CRYPTO' and p['profit_factor_state'] == 'NO_LOSSES'
    assert p['source_kind'] == 'UNVERIFIED_SIMULATOR_INPUT_PROVENANCE'
    assert report['orders'] == report['network_requests'] == 0 and report['promotion'] == 'NONE'
    assert j.ingest_outcomes(equity)['inserted'] == j.ingest_proxy_outcomes(proxies)['inserted'] == 0


def test_new_review_receives_metrics_while_cached_review_stays_frozen(tmp_path):
    j,equity,_ = journal_with_outcomes(tmp_path)
    j.ingest(experiment()); client = Client()
    old = j.review('trial-v1',client=client)
    assert client.calls[0][1]['observational_outcome_feedback']['groups'][0]['mean_net_pnl'] == '19.00'
    assert client.calls[0][1]['quote_simulation_feedback']['groups'][0]['win_rate'] == '1'
    loss = deepcopy(equity[0]); loss['lifecycle'] += '-loss'; loss['net_pnl'] = '-40'
    j.ingest_outcomes([loss])
    assert j.outcome_feedback()['groups'][0]['mean_net_pnl'] == '-10.50'
    assert j.review('trial-v1')['outcome_feedback'] == old['outcome_feedback']
    assert len(client.calls) == 1


def test_missing_unrelated_and_old_comparison_only_journals_not_changed(tmp_path):
    missing = tmp_path/'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError): read_performance(missing)
    assert not missing.exists()
    unrelated = tmp_path/'unrelated.sqlite3'
    with sqlite3.connect(unrelated) as db: db.execute('CREATE TABLE orders(id TEXT)')
    before = unrelated.read_bytes()
    with pytest.raises(ValueError,match='Dedicated'): read_performance(unrelated)
    assert unrelated.read_bytes() == before
    old = tmp_path/'old.sqlite3'
    with sqlite3.connect(old) as db: db.execute('CREATE TABLE learning_experiments_v1(id TEXT)')
    before = old.read_bytes()
    r = read_performance(old)
    assert r['equity']['groups'] == r['simulations']['groups'] == [] and old.read_bytes() == before


def test_cli_is_read_only_and_never_calls_configured_model(tmp_path, monkeypatch, capsys):
    j,_,_ = journal_with_outcomes(tmp_path)
    before = j.path.read_bytes()
    monkeypatch.setenv('XAI_API_KEY','do-not-call')
    assert main(['--journal',str(j.path)]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r['orders'] == r['network_requests'] == 0 and j.path.read_bytes() == before
