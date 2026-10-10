import threading
import time

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import EngineWorker, WorkerState, _decode, _encode, _quote
from app.asset_modules.worker_check import check_workers


def wait_reply(worker):
    until = time.monotonic() + 12
    while time.monotonic() < until:
        reply = worker.poll()
        if reply:
            return reply
        if worker.state == WorkerState.FAILED:
            pytest.fail(str(worker.status()))
        time.sleep(0.01)
    pytest.fail("Worker did not reply")


@pytest.fixture
def worker():
    instance = EngineWorker(EngineId.WARRIOR, policy_version="TEST_V1", timeout_seconds=10)
    instance.start()
    assert wait_reply(instance)["kind"] == "READY"
    yield instance
    instance.close()
    assert not instance._process.is_alive()


def test_five_independent_processes_and_clean_shutdown():
    statuses = check_workers()
    assert {status["engine"] for status in statuses} == {engine.value for engine in EngineId}
    assert len({status["pid"] for status in statuses}) == 5
    assert all(status["state"] == "READY" and status["execution_enabled"] is False for status in statuses)


def test_observation_roundtrip_and_no_queue(worker):
    payload = {"symbol": "SPY", "bid": "500.10", "ask": "500.11"}
    assert worker.submit("OBSERVE", payload)
    with pytest.raises(RuntimeError, match="not queued"):
        worker.submit("PING")
    reply = wait_reply(worker)
    assert reply["payload"] == {**payload, "samples": 1}
    worker.submit("OBSERVE", payload)
    assert wait_reply(worker)["payload"]["samples"] == 2
    assert worker.submit("PING")
    assert wait_reply(worker)["payload"]["symbols"] == 1


def test_instrument_history_is_bounded(worker):
    for index in range(129):
        worker.submit("OBSERVE", {"symbol": "S" + str(index), "bid": "1", "ask": "2"})
        wait_reply(worker)
    worker.submit("PING")
    assert wait_reply(worker)["payload"]["symbols"] == 128
    worker.submit("OBSERVE", {"symbol": "S0", "bid": "1", "ask": "2"})
    assert wait_reply(worker)["payload"]["samples"] == 1


@pytest.mark.parametrize("kind", ["BUY", "SELL", "EXEC", "REWRITE", "POLICY_CHANGE"])
def test_execution_and_code_mutation_commands_are_not_supported(worker, kind):
    with pytest.raises(ValueError, match="Only observation"):
        worker.submit(kind)
    assert worker.state == WorkerState.READY


@pytest.mark.parametrize("payload", [
    {}, {"symbol": "SPY", "bid": "1", "ask": "2", "order": "BUY"},
    {"symbol": "bad symbol", "bid": "1", "ask": "2"},
    {"symbol": "SPY", "bid": "NaN", "ask": "2"},
    {"symbol": "SPY", "bid": "0", "ask": "2"},
    {"symbol": "SPY", "bid": "3", "ask": "2"},
    {"symbol": "SPY", "bid": 1, "ask": "2"},
    {"symbol": "SPY", "bid": "1e-9", "ask": "2"},
    {"symbol": "SPY", "bid": "1", "ask": "1e9999"},
])
def test_bad_observation_is_rejected_before_transport(payload):
    with pytest.raises(ValueError):
        _quote(payload)


def test_message_size_and_duplicate_keys():
    with pytest.raises(ValueError, match="byte limit"):
        _encode({"data": "x" * 8192})
    with pytest.raises(ValueError, match="Duplicate"):
        _decode(b'{"engine":"CRYPTO","engine":"OPTIONS"}')
    with pytest.raises(ValueError):
        _decode(b'[]')


def test_child_crash_does_not_restart_or_replay(worker):
    pid = worker.pid
    worker._process.terminate()
    worker._process.join(timeout=2)
    worker.poll()
    assert worker.state == WorkerState.FAILED
    assert worker.pid == pid
    with pytest.raises(RuntimeError):
        worker.submit("PING")
    with pytest.raises(RuntimeError):
        worker.start()


def test_late_reply_is_not_accepted(worker):
    worker.submit("PING")
    worker._deadline = time.monotonic() - 1
    assert worker.poll() is None
    assert worker.state == WorkerState.FAILED
    assert worker.failure_reason == "RESPONSE_TIMEOUT"
    assert not worker._process.is_alive()


@pytest.mark.parametrize("field,value", [("engine", "OPTIONS"), ("session", "other"), ("policy", "OTHER"), ("sequence", 2)])
def test_child_rejects_foreign_identity_and_replayed_sequence(worker, field, value):
    message = {"engine": worker.engine.value, "session": worker.session_id, "policy": worker.policy_version,
               "sequence": 1, "kind": "PING", "payload": {}}
    message[field] = value
    worker._connection.send_bytes(_encode(message))
    until = time.monotonic() + 3
    while worker.state == WorkerState.READY and time.monotonic() < until:
        worker.poll()
        time.sleep(0.01)
    assert worker.state == WorkerState.FAILED


def test_policy_and_identity_are_read_only(worker):
    for name in ("engine", "session_id", "policy_version"):
        with pytest.raises(AttributeError):
            setattr(worker, name, "OTHER")


@pytest.mark.parametrize("mutation,reason", [
    ({"engine": "CRYPTO"}, "RESPONSE_IDENTITY_MISMATCH"),
    ({"sequence": 99}, "RESPONSE_IDENTITY_MISMATCH"),
    ({"payload": {"pid": -1, "symbols": 0}}, "INVALID_RESPONSE_PAYLOAD"),
    ({"payload": {"pid": 1, "symbols": 129}}, "INVALID_RESPONSE_PAYLOAD"),
])
def test_parent_refuses_forged_response(worker, mutation, reason):
    class InjectedReply:
        def poll(self): return True
        def recv_bytes(self, limit): return _encode(reply)
        def close(self): pass
    worker.submit("PING")
    reply = {"engine": worker.engine.value, "session": worker.session_id, "policy": worker.policy_version,
             "sequence": 1, "kind": "PONG", "payload": {"pid": worker.pid, "symbols": 0}}
    reply.update(mutation)
    connection = worker._connection
    worker._connection = InjectedReply()
    try:
        assert worker.poll() is None
        assert worker.state == WorkerState.FAILED
        assert worker.failure_reason == reason
    finally:
        connection.close()


def test_cross_thread_handle_use_is_refused(worker):
    failures = []
    def attempt():
        try:
            worker.submit("PING")
        except RuntimeError as error:
            failures.append(str(error))
    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(timeout=2)
    assert len(failures) == 1
    assert worker.state == WorkerState.READY


def test_close_is_idempotent_and_start_is_once(worker):
    with pytest.raises(RuntimeError):
        worker.start()
    worker.close()
    worker.close()
    assert worker.state == WorkerState.CLOSED
    assert worker.poll() is None


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf"), 31, True])
def test_invalid_deadline(timeout):
    with pytest.raises(ValueError):
        EngineWorker(EngineId.SCALPER, policy_version="V1", timeout_seconds=timeout)
