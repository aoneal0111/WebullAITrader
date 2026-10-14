from dataclasses import replace
import json
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from app.asset_modules.learning_collector import LearningOutcomeCollector, main, source_paths
from app.asset_modules.grok_learning import LearningJournal
from app.composition.learning_collection import create_learning_collector
from app.composition.runtime_mode import RuntimeMode
from app.configuration.loader import load_configuration
from tests.asset_modules.test_learning_outcomes import ledger, order
from tests.asset_modules.test_crypto_learning_outcomes import native_ledger
from tests.asset_modules.test_contract_learning_outcomes import closed_ledger


def collector(tmp_path):
    equity = ledger(tmp_path, [order('BUY','10','100'),order('SELL','10','102')])
    crypto, _ = native_ledger(tmp_path)
    options = closed_ledger(tmp_path, 'OPTIONS')
    futures = closed_ledger(tmp_path, 'FUTURES')
    return LearningOutcomeCollector(tmp_path/'learning.sqlite3',
        dict(EQUITY=equity, CRYPTO=crypto, OPTIONS=options, FUTURES=futures))


def test_all_engine_collection_is_read_only_idempotent_and_separates_evidence(tmp_path):
    c = collector(tmp_path)
    before = {k:p.read_bytes() for k,p in c.sources.items()}
    assert c.snapshot()['state'] == 'READY' and not c.journal_path.exists()
    first = c.collect_once()
    assert first['state'] == 'COLLECTED'
    assert all(s['inserted'] == 1 for s in first['sources'].values())
    second = c.collect_once()
    assert all(s['inserted'] == 0 and s['already_present'] == 1 for s in second['sources'].values())
    assert before == {k:p.read_bytes() for k,p in c.sources.items()}
    j = LearningJournal(c.journal_path)
    assert j.outcome_feedback()['groups'][0]['closed_lifecycles'] == 1
    assert {g['engine'] for g in j.proxy_outcome_feedback()['groups']} == {'CRYPTO','OPTIONS','FUTURES'}
    assert c.snapshot()['cycles'] == 2 and j.review_next()['state'] == 'IDLE'
    first['sources']['EQUITY']['inserted'] = 999
    snap = c.snapshot(); snap['last_cycle']['sources']['EQUITY']['inserted'] = 999
    assert c.snapshot()['last_cycle']['sources']['EQUITY']['inserted'] == 0
    assert c.close()


def test_missing_sources_are_not_created_and_other_sources_still_import(tmp_path):
    c = collector(tmp_path)
    c.sources['OPTIONS'].unlink(); c.sources['FUTURES'].unlink()
    result = c.collect_once()
    assert result['state'] == 'COLLECTED'
    assert result['sources']['OPTIONS'] == result['sources']['FUTURES'] == {'state':'MISSING_SOURCE'}
    assert result['sources']['EQUITY']['inserted'] == 1
    assert not c.sources['OPTIONS'].exists() and not c.sources['FUTURES'].exists()


def test_source_failure_is_isolated_sanitized_and_retried_next_cycle(tmp_path, monkeypatch, caplog):
    import app.asset_modules.learning_collector as module
    c = collector(tmp_path)
    original = module.read_crypto_outcomes
    def fail(_): raise ValueError('private-key-and-response-body')
    monkeypatch.setattr(module,'read_crypto_outcomes',fail)
    report = c.collect_once()
    assert report['state'] == 'PARTIAL_SOURCE_FAILURE'
    assert report['sources']['CRYPTO'] == {'state':'READ_FAILED','error_class':'ValueError'}
    assert report['sources']['EQUITY']['inserted'] == report['sources']['FUTURES']['inserted'] == 1
    assert 'private-key' not in json.dumps(report) and 'private-key' not in caplog.text
    monkeypatch.setattr(module,'read_crypto_outcomes',original)
    assert c.collect_once()['sources']['CRYPTO']['inserted'] == 1


def test_import_conflict_does_not_rewrite_outcomes_or_block_other_engines(tmp_path):
    import sqlite3
    c = collector(tmp_path); c.collect_once()
    with sqlite3.connect(c.sources['EQUITY']) as db:
        raw = db.execute('SELECT payload FROM orders WHERE order_id=2').fetchone()[0]
        value = json.loads(raw); value['fills'][0]['price'] = '200'
        db.execute('UPDATE orders SET payload=? WHERE order_id=2',(json.dumps(value),))
    r = c.collect_once()
    assert r['sources']['EQUITY']['state'] == 'IMPORT_FAILED'
    assert r['sources']['CRYPTO']['already_present'] == 1
    assert LearningJournal(c.journal_path).outcome_feedback()['groups'][0]['net_pnl'] == '19.00'


def test_worker_is_background_nonoverlapping_and_stop_retains_busy_ownership(tmp_path, monkeypatch):
    import app.asset_modules.learning_collector as module
    c = collector(tmp_path)
    entered, release = Event(), Event()
    identities = []
    original = module.read_equity_outcomes
    def blocked(path):
        identities.append(get_ident()); entered.set()
        assert release.wait(5)
        return original(path)
    monkeypatch.setattr(module,'read_equity_outcomes',blocked)
    try:
        assert c.start() and entered.wait(2)
        assert identities == [c._thread.ident] and identities[0] != get_ident()
        assert c.start()  # Same worker, no second reader.
        assert c.collect_once()['state'] == 'BUSY'
        assert not c.close(timeout_seconds=0)
        assert c.snapshot()['state'] == 'STOPPING' and not c.start()
        release.set(); c._thread.join(2)
        assert c.close() and c.snapshot()['state'] == 'STOPPED'
        assert LearningJournal(c.journal_path).outcome_feedback()['groups'] == []
    finally:
        release.set(); c.close()


def test_worker_wait_is_interruptible_and_restart_requires_new_instance(tmp_path):
    c = collector(tmp_path)
    finished = Event()
    original = c.collect_once
    def collect():
        result = original(); finished.set(); return result
    c.collect_once = collect
    assert c.start() and finished.wait(2)
    assert c.close() and not c.start()
    restored = LearningOutcomeCollector(c.journal_path, c.sources)
    assert all(s['inserted'] == 0 for s in restored.collect_once()['sources'].values())


def test_worker_start_failure_is_reported_without_throwing_or_creating_journal(tmp_path, monkeypatch):
    import app.asset_modules.learning_collector as module
    c = collector(tmp_path)
    monkeypatch.setattr(module, 'Thread', lambda **kwargs: (_ for _ in ()).throw(RuntimeError('private')))
    assert not c.start() and c.snapshot()['state'] == 'UNAVAILABLE'
    assert not c.journal_path.exists() and c.close()


@pytest.mark.parametrize('interval',[0,1,59,3601,True,'60'])
def test_invalid_intervals_are_refused_before_journal_creation(tmp_path, interval):
    with pytest.raises(ValueError):
        LearningOutcomeCollector(tmp_path/'learning.db', source_paths(tmp_path/'equity.db'), interval_seconds=interval)
    assert not (tmp_path/'learning.db').exists()


def test_aliases_are_refused_including_hard_links(tmp_path):
    c = collector(tmp_path)
    with pytest.raises(ValueError,match='distinct'):
        LearningOutcomeCollector(c.sources['EQUITY'], c.sources)
    alias = tmp_path/'alias.db'; alias.hardlink_to(c.sources['CRYPTO'])
    with pytest.raises(ValueError,match='alias'):
        LearningOutcomeCollector(alias, c.sources)
    assert not c.journal_path.exists()


def test_journal_error_refuses_cycle_and_isolated_cli_is_offline(tmp_path, monkeypatch, capsys):
    c = collector(tmp_path)
    c.journal_path.write_bytes(b'not SQLite')
    before = c.journal_path.read_bytes()
    assert c.collect_once()['state'] == 'JOURNAL_UNAVAILABLE'
    assert c.journal_path.read_bytes() == before
    monkeypatch.setenv('XAI_API_KEY','do-not-call')
    assert main(['--execution-ledger',str(c.sources['EQUITY']),
                 '--journal',str(tmp_path/'separate-learning.sqlite3')]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r['sources']['EQUITY']['inserted'] == 1 and r['orders'] == r['network_requests'] == 0


def config(tmp_path, **changes):
    return load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'EXECUTION_DATABASE_PATH':str(tmp_path/'configured'/'execution.db'),
        'ATLAS_LEARNING_OUTCOME_COLLECTION_ENABLED':'true',
        'ATLAS_LEARNING_OUTCOME_JOURNAL_PATH':str(tmp_path/'learning.sqlite3'), **changes})


def test_configuration_defaults_inert_and_factory_uses_actual_durable_equity_path(tmp_path):
    disabled = load_configuration({})
    assert not disabled.learning_outcome_collection_enabled
    assert create_learning_collector(disabled,RuntimeMode.PAPER,paper_persistence_path=None) is None
    c = create_learning_collector(config(tmp_path), RuntimeMode.PAPER,
                                 paper_persistence_path=tmp_path/'actual'/'equity.db')
    assert c.sources['EQUITY'] == (tmp_path/'actual'/'equity.db').resolve()
    assert c.sources['CRYPTO'] == (tmp_path/'configured'/'crypto-paper.sqlite3').resolve()
    assert c.snapshot()['state'] == 'READY' and not c.journal_path.exists()
    assert c.close()


@pytest.mark.parametrize('change',['live','environment','runtime','missing_path'])
def test_invalid_activation_refused_before_files_or_threads(tmp_path, change):
    from app.configuration.models import TradingEnvironment
    c, mode, path = config(tmp_path), RuntimeMode.PAPER, tmp_path/'equity.db'
    if change == 'live': c = replace(c,live_trading_enabled=True)
    elif change == 'environment': c = replace(c,environment=TradingEnvironment.TEST)
    elif change == 'runtime': mode = RuntimeMode.SIMULATED
    else: path = None
    with pytest.raises(ValueError,match='requires PAPER'):
        create_learning_collector(c,mode,paper_persistence_path=path)
    assert not c.learning_outcome_journal_path.exists()


def test_real_desktop_composition_is_inert_and_owns_collector_shutdown(tmp_path, monkeypatch):
    import app.composition.desktop as module
    c = config(tmp_path, WARRIOR_FORWARD_CAPTURE_PATH=str(tmp_path/'capture.db'),
        CRYPTO_DISCOVERY_PATH=str(tmp_path/'crypto.jsonl'),
        ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH=str(tmp_path/'entry-experiment.sqlite3'))
    monkeypatch.setattr(module,'load_configuration',lambda:c)
    composition = module.create_desktop_composition(paper_persistence_path=tmp_path/'equity.db')
    try:
        collector = composition.learning_collector
        assert collector is not None and collector.snapshot()['state'] == 'READY'
        assert not c.learning_outcome_journal_path.exists()
        assert collector.sources['EQUITY'] == composition.paper_trading_commands.durable_store.path.resolve()
    finally: composition.close()
    assert collector.snapshot()['state'] == 'STOPPED'


def test_gui_launch_starts_collection_after_show_and_teardown_owns_close(tmp_path, monkeypatch):
    import app.gui.app as module
    calls = []
    app = SimpleNamespace(aboutToQuit=SimpleNamespace(connect=lambda cb:None),
        setApplicationName=lambda n:None,setOrganizationName=lambda n:None,
        exec=lambda:(calls.append('exec') or 0))
    runtime = SimpleNamespace(note_shutdown_request=lambda **k:None)
    values = {k:None for k in ('bus','state_store','trading_service','order_command_factory',
        'chart_market_data_service','chart_default_symbol','warrior_forward_sidecar')}
    composition = SimpleNamespace(**values,runtime_service=runtime,
        learning_collector=SimpleNamespace(start=lambda:calls.append('collector_start')),
        close=lambda:calls.append('composition_close'))
    supervisor = SimpleNamespace(close=lambda:True,paper=SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(module,'load_dotenv',lambda **k:None)
    monkeypatch.setattr(module,'load_configuration',lambda:config(tmp_path))
    monkeypatch.setattr(module,'configure_logging',lambda:None)
    monkeypatch.setattr(module,'acquire_runtime_ownership',lambda p:SimpleNamespace(release=lambda:None))
    monkeypatch.setattr(module,'QApplication',lambda args:app)
    monkeypatch.setattr(module,'create_desktop_composition',lambda **k:composition)
    monkeypatch.setattr(module,'create_asset_modules',lambda c:SimpleNamespace(crypto_supervisor=supervisor))
    monkeypatch.setattr(module,'MainWindow',lambda *a,**k:SimpleNamespace(show=lambda:calls.append('show')))
    assert module.main() == 0
    assert calls == ['show','collector_start','exec','composition_close']
