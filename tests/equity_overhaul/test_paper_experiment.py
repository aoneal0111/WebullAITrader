from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,configured,ENV
from app.equity_overhaul.admission import QualifiedEquityTradingService
from app.configuration import load_configuration
from tests.equity_overhaul.test_admission import placement
from tests.test_support.session_clock import create_session_paper_composition

NOW=datetime(2026,10,29,13,30,tzinfo=UTC)


def policy(tmp_path):
    create(tmp_path,lambda:NOW);path,spec=load(tmp_path/'experiment.json',lambda:NOW)
    p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
    p.capture_ready=lambda:True
    p.bind(SimpleNamespace(_code={'WARRIOR_MOMENTUM_V1':'a'*64,'QUICK_SCALPER':'c'*64},
        _configuration={'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64}))
    return p


def metadata(p):
    return dict(strategy_trial_id=p.spec['trial_id'],strategy_owner='WARRIOR_MOMENTUM',strategy_family='FIRST_PULLBACK',
        strategy_code_fingerprint='a'*64,strategy_configuration_fingerprint='b'*64,
        source='autonomous-paper',reason='ENTRY',structural_stop='9.95',risk_dollars='0.5')


def test_trial_never_becomes_a_release_promotion_and_requires_exact_family_owner_hashes(tmp_path):
    p=policy(tmp_path);m=metadata(p)
    assert p.permits(m) and p.status()['approved_policies']==[]
    for key,value in [('strategy_trial_id','wrong'),('strategy_owner','QUICK_SCALPER'),('strategy_family','UNKNOWN'),('strategy_code_fingerprint','f'*64)]:
        assert not p.permits({**m,key:value})
    p.capture_ready=lambda:False
    assert not p.permits(m)


def test_expiry_revocation_and_clock_discontinuity_block_entries(tmp_path):
    p=policy(tmp_path);m=metadata(p)
    p.clock=lambda:NOW+timedelta(seconds=3)
    assert not p.permits(m)
    p.clock=lambda:NOW+timedelta(hours=8)
    assert not p.permits(m)
    p.clock=lambda:NOW
    p.path.unlink()
    assert not p.permits(m)


def test_request_caps_persist_across_restart_and_duplicates_cannot_consume_twice(tmp_path):
    p=policy(tmp_path);c=create_session_paper_composition()
    try:
        m=metadata(p);request=placement(c.order_command_factory,metadata=m)
        assert p.claim_order(request)
        assert not p.claim_order(request)
        restarted=PaperExperiment(p.path,p.spec,clock=lambda:NOW,timer=lambda:0)
        restarted.capture_ready=lambda:True;restarted._bindings=p._bindings
        for _ in range(49):assert restarted.claim_order(placement(c.order_command_factory,metadata=m))
        assert not restarted.claim_order(placement(c.order_command_factory,metadata=m))
        with closing(sqlite3.connect(p.db_path)) as db:
            assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==50
    finally:c.close()


@pytest.mark.parametrize('field,value',[('risk_dollars','2'),('structural_stop','9'),('reason','ADD_ON'),('source','grok')])
def test_geometry_addons_and_grok_dispatch_cannot_bypass_trial_caps(tmp_path,field,value):
    p=policy(tmp_path);c=create_session_paper_composition()
    try:assert not p.claim_order(placement(c.order_command_factory,metadata={**metadata(p),field:value}))
    finally:c.close()


def test_wrapper_bounds_gateway_deadline_and_protective_sells_survive_revocation(tmp_path):
    p=policy(tmp_path);c=create_session_paper_composition()
    try:
        native=Mock();wrapped=QualifiedEquityTradingService(native,p)
        request=placement(c.order_command_factory,metadata=metadata(p))
        wrapped.place_order(request)
        sent=native.place_order.call_args.args[0]
        assert datetime.fromisoformat(sent.order.metadata['chase_deadline'])==NOW+timedelta(seconds=60)
        p.path.unlink();native.reset_mock()
        assert not wrapped.place_order(placement(c.order_command_factory,metadata=metadata(p))).success
        native.place_order.assert_not_called()
        wrapped.place_order(placement(c.order_command_factory,'SELL'));native.place_order.assert_called_once()
        wrapped.cancel_order('cancel');native.cancel_order.assert_called_once()
    finally:c.close()


def test_configuration_requires_recording_and_paper_with_live_and_grok_orders_off(tmp_path,monkeypatch):
    create(tmp_path);monkeypatch.setenv(ENV,str(tmp_path/'experiment.json'))
    c=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','ATLAS_DAILY_RESEARCH_ENABLED':'true'})
    assert configured(c,'PAPER') is not None
    for changed in (replace(c,live_trading_enabled=True),replace(c,grok_paper_entries_enabled=True),replace(c,daily_research_enabled=False)):
        with pytest.raises(ValueError):configured(changed,'PAPER')
    with pytest.raises(ValueError):configured(c,'LIVE')


def test_real_desktop_wiring_caps_both_engines_and_capture(monkeypatch,tmp_path,historical_intelligence_artifact):
    import app.composition.desktop as module
    create(tmp_path/'trial');monkeypatch.setenv(ENV,str(tmp_path/'trial/experiment.json'))
    c=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','ATLAS_DAILY_RESEARCH_ENABLED':'true',
        'WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'capture.sqlite3'),'ALLOWED_SYMBOLS':'XYZ'})
    monkeypatch.setattr(module,'load_configuration',lambda:c)
    graph=module.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3')
    try:
        trial=graph.equity_paper_experiment
        assert trial.active()
        assert graph.quick_scalper_runtime.risk_config.maximum_quantity==10
        assert graph.warrior_forward_sidecar.strategy_config.risk.maximum_position_dollars==D(100)
        assert graph.daily_research.journal.max_bytes==4*1024**3
        assert (tmp_path/'trial/recording-session.json').exists()
        assert graph.autonomous_paper_bridge.entry_qualification is not None
    finally:graph.close(timeout_seconds=1)


def test_trial_order_reaches_actual_paper_gateway_and_is_durably_attributed(tmp_path):
    p=policy(tmp_path);c=create_session_paper_composition()
    try:
        wrapped=QualifiedEquityTradingService(c.trading_service,p)
        result=wrapped.place_order(placement(c.order_command_factory,metadata=metadata(p)))
        assert result.success
        entry=c.order_book.history()[-1]
        assert entry.request.metadata['strategy_trial_id']==p.spec['trial_id']
        assert entry.request.quantity==D(10)
        assert entry.request.entry_valid_until<=NOW+timedelta(seconds=60)
    finally:c.close()
