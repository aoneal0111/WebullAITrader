import json
from threading import Event, Thread

import pytest

from app.live_scanner.authoritative_lane import AuthoritativeEventLane, AuthoritativeLaneFailure
from app.performance_diagnostics import PerformanceDiagnostics
from app.exception_diagnostics import safe_exception_details


def test_failure_during_stop_does_not_leave_queue_join_hanging():
    entered, release = Event(), Event()
    original = ValueError('synthetic invariant')
    def observer(event):
        entered.set()
        release.wait(2)
        raise original
    lane = AuthoritativeEventLane(observer, capacity=4)
    lane.start(); lane.publish(1)
    assert entered.wait(1)
    lane.publish(2); lane.publish(3)
    drain_started=Event()
    original_join=lane._queue.join
    original_put=lane._queue.put
    def join():
        drain_started.set();release.set();original_join()
    def put(item,*args,**kwargs):
        if item is None:drain_started.set();release.set()
        return original_put(item,*args,**kwargs)
    lane._queue.join=join
    lane._queue.put=put
    stopped, errors = Event(), []
    def stop():
        try:lane.stop(timeout=1)
        except Exception as error:errors.append(error)
        finally:stopped.set()
    thread=Thread(target=stop,daemon=True);thread.start()
    assert drain_started.wait(1)
    assert stopped.wait(1.5)
    assert not errors
    assert lane.metrics()['authoritative_events_abandoned']==2
    assert lane.metrics()['authoritative_events_dequeued']==1
    with pytest.raises(AuthoritativeLaneFailure) as error:lane.publish(4)
    assert error.value.__cause__ is original
    lane.stop()


def test_slow_worker_stop_has_a_real_deadline_and_can_finish_later():
    entered, release = Event(), Event()
    def observer(event):entered.set();release.wait(2)
    lane=AuthoritativeEventLane(observer,capacity=1)
    lane.start();lane.publish(1);assert entered.wait(1)
    with pytest.raises(RuntimeError,match='timed out'):lane.stop(timeout=0.01)
    release.set();lane.stop(timeout=1)
    assert lane.metrics()['authoritative_events_abandoned']==0


def test_innermost_failure_and_chain_survive_cleanup_checkpoint(tmp_path):
    d=PerformanceDiagnostics();path=tmp_path/'performance.json'
    d.start_run(artifact_path=path)
    try:
        with pytest.raises(RuntimeError):
            with d.authoritative_stage('authoritative_worker_event',symbol='TEST'):
                with d.authoritative_stage('paper_position_protection',symbol='TEST'):
                    try:raise KeyError('synthetic_field')
                    except KeyError as cause:raise RuntimeError('synthetic wrapper') from cause
        # A healthy later stage/cleanup cannot replace the failure record.
        with d.authoritative_stage('cleanup'):pass
    finally:d.finish_run()
    result=json.loads(path.read_text())['metrics']['authoritative_failure']
    assert result['stage']=='paper_position_protection'
    assert [x['exception_type'] for x in result['chain']]==['RuntimeError','KeyError']
    assert result['chain'][1]['frames'][-1]['function']=='test_innermost_failure_and_chain_survive_cleanup_checkpoint'
    assert 'synthetic_field' in result['chain'][1]['message']


def test_sensitive_nested_exception_redacts_chain_and_never_copies_locals():
    try:
        try:raise ValueError('token=private-fixture')
        except ValueError as cause:raise RuntimeError('outer') from cause
    except RuntimeError as error:result=safe_exception_details(error)
    raw=json.dumps(result)
    assert 'private-fixture' not in raw
    assert all(item['message']=='[REDACTED]' for item in result['chain'])
    assert all('locals' not in f and '/' not in f['file'] for x in result['chain'] for f in x['frames'])
