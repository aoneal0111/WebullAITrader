"""Transient read outages must not terminate continuous PAPER or fake freshness."""
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace as NS
import json
import pytest

from app.configuration import TradingEnvironment
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.webull.errors import NetworkError, AuthenticationError
from tests.test_equity_network_recovery import driver, Stop
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment,ACCOUNT_SIZING_PROFILE

NOW=datetime(2026,11,13,tzinfo=UTC)


def persistent(tmp_path,poll):
    d=driver(poll);d._continuous_paper_root=tmp_path;d._clock=lambda:NOW
    return d


def test_more_than_three_transient_failures_recover_with_capped_backoff(tmp_path):
    calls=[];snapshot=object()
    def poll(*args,**kwargs):
        calls.append(1)
        if len(calls)<9:raise NetworkError('fixture',retryable=True)
        return snapshot
    d=persistent(tmp_path,poll);stop=Stop()
    assert d._poll_account_with_retry(stop) is snapshot
    assert len(calls)==9 and stop.delays==[1,2,4,8,16,30,30,30]
    assert (tmp_path/'ACCOUNT_DATA_UNAVAILABLE').exists()
    # A returned object alone cannot clear the pause; publication must finish.
    assert json.loads((tmp_path/'account-recovery-status.json').read_text())['account_available'] is False


def test_recovered_snapshot_published_once_before_unpausing(tmp_path):
    snapshot=object();count=[0];published=[];stop=Stop()
    def poll(*args,**kwargs):
        count[0]+=1
        if count[0]<5:raise NetworkError('fixture',retryable=True)
        return snapshot
    d=persistent(tmp_path,poll)
    d._configuration=NS(environment=TradingEnvironment.PAPER,reconciliation_interval_seconds=1)
    d._market_event_observer=None;d._cycles_completed=0
    d._retry_scanner_after_session_transition=lambda _:None
    def sink(value):
        assert (tmp_path/'ACCOUNT_DATA_UNAVAILABLE').exists()
        published.append(value)
    d._account_snapshot_sink=sink
    d._poll_accounts(stop_event=stop,cycle_sink=lambda _:stop.set())
    assert published==[snapshot]
    assert not d._account_data_unavailable()
    assert d._health[-1][2].execution_status=='ENABLED'
    assert json.loads((tmp_path/'account-recovery-status.json').read_text())['account_available'] is True


def test_sink_failure_does_not_clear_account_pause(tmp_path):
    d=persistent(tmp_path,lambda *a,**kw:object())
    (tmp_path/'ACCOUNT_DATA_UNAVAILABLE').touch()
    d._configuration=NS(environment=TradingEnvironment.PAPER,reconciliation_interval_seconds=1)
    d._market_event_observer=None
    d._account_snapshot_sink=lambda _:(_ for _ in ()).throw(ValueError('projection'))
    with pytest.raises(ValueError):d._poll_accounts(stop_event=Stop(),cycle_sink=lambda _:None)
    assert d._account_data_unavailable()


def test_operator_stop_interrupts_outage_and_nontransient_failure_still_raises(tmp_path):
    def poll(*args,**kwargs):raise NetworkError('fixture',retryable=True)
    class Interrupted(Stop):
        def wait(self,timeout=None):self.set();return True
    assert persistent(tmp_path,poll)._poll_account_with_retry(Interrupted()) is None
    def denied(*args,**kwargs):raise AuthenticationError('private body')
    with pytest.raises(AuthenticationError):persistent(tmp_path,denied)._poll_account_with_retry(Stop())
    assert 'private body' not in (tmp_path/'account-recovery-status.json').read_text()


def test_new_entry_policy_pauses_while_recovery_marker_exists(tmp_path):
    create(tmp_path,clock=lambda:NOW,profile=ACCOUNT_SIZING_PROFILE,continuous=True)
    path,spec=load(tmp_path/'experiment.json',clock=lambda:NOW)
    exp=PaperExperiment(path,spec,clock=lambda:NOW);exp.capture_ready=lambda:True
    assert exp.active()
    marker=tmp_path/'ACCOUNT_DATA_UNAVAILABLE';marker.touch();assert not exp.active()
    assert exp.status()['protective_sells']=='AVAILABLE'
    marker.unlink();assert exp.active()


def test_feed_recovery_cannot_enable_entries_while_account_unavailable(tmp_path):
    d=persistent(tmp_path,lambda *a,**kw:None)
    d._market_data_transport=lambda:None
    (tmp_path/'ACCOUNT_DATA_UNAVAILABLE').touch()
    d._complete_feed_recovery()
    assert d._health[-1][2].execution_status=='BLOCKED - ACCOUNT DATA'
    assert d._health[-1][2].runtime_status=='DEGRADED'


def test_legacy_paper_and_live_keep_existing_retry_contract():
    calls=[]
    def poll(*args,**kwargs):calls.append(1);raise NetworkError('fixture',retryable=True)
    with pytest.raises(NetworkError):driver(poll)._poll_account_with_retry(Stop())
    assert len(calls)==3
    calls.clear();d=driver(poll,TradingEnvironment.PRODUCTION)
    d._continuous_paper_root=object()  # A path alone cannot opt LIVE into retries.
    with pytest.raises(NetworkError):d._poll_account_with_retry(Stop())
    assert len(calls)==1


def test_primary_failure_is_preserved_and_cleanup_is_deliberate():
    from unittest.mock import Mock
    d=object.__new__(DesktopBrokerRuntimeDriver)
    d._configuration=NS(environment=TradingEnvironment.PAPER)
    d._market_event_observer=None;d._startup_validator=None;d._startup_validation=None
    d._broker=NS(connect=lambda:None);d._publish=Mock()
    for name in ('_start_projection_worker','_start_market_data','_stop_background_research',
                 '_stop_dynamic_momentum_discovery','_stop_observer','_stop_projection_worker','_disconnect'):
        setattr(d,name,Mock())
    failure=NetworkError('fixture',retryable=True)
    d._poll_accounts=Mock(side_effect=failure)
    states=[];d._stop_market_data=lambda:states.append(d._shutdown_requested)
    with pytest.raises(NetworkError) as caught:d.run(stop_event=Event(),cycle_sink=lambda _:None)
    assert caught.value is failure and states==[True]


@pytest.mark.parametrize('continuous',[False,True])
def test_constructor_opts_in_only_for_validated_persistent_operation(tmp_path,monkeypatch,continuous):
    from tests.test_desktop_broker_runtime import configuration,broker_runtime,FakeBroker
    from app.equity_overhaul.paper_experiment import ENV
    create(tmp_path,clock=lambda:NOW,profile=ACCOUNT_SIZING_PROFILE,continuous=continuous)
    monkeypatch.setenv(ENV,str(tmp_path/'experiment.json'))
    broker=FakeBroker()
    d=DesktopBrokerRuntimeDriver(configuration=configuration(),broker_runtime=broker_runtime(broker),
        event_sink=lambda _:None,account_snapshot_sink=lambda _:None)
    assert (d._continuous_paper_root==tmp_path)==continuous
    assert (tmp_path/'ACCOUNT_DATA_UNAVAILABLE').exists()==continuous
    assert broker.calls==[]
