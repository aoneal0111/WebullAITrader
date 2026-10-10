"""Bounded observation-only process boundary; no broker or model imports."""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import re
import time
import threading
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import uuid4

from app.asset_modules.engine_catalog import EngineId

MAX_MESSAGE_BYTES = 8192
MAX_SYMBOLS = 128
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./_-]{0,63}\Z")


class WorkerState(StrEnum):
    NEW = "NEW"
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


def _encode(message):
    data = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("Worker message exceeds byte limit")
    return data


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate message key")
            result[key] = value
        return result
    message = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(message, dict):
        raise ValueError("Worker message must be an object")
    return message


def _quote(payload):
    if not isinstance(payload, dict) or set(payload) != {"symbol", "bid", "ask"}:
        raise ValueError("Invalid observation fields")
    symbol = payload["symbol"]
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid instrument identity")
    prices = []
    for name in ("bid", "ask"):
        value = payload[name]
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Prices must be bounded decimal strings")
        try:
            price = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("Invalid price") from error
        if not price.is_finite() or price <= 0 or price.adjusted() > 12 or price.as_tuple().exponent < -8:
            raise ValueError("Invalid price range")
        prices.append(price)
    if prices[1] < prices[0]:
        raise ValueError("Crossed observation")
    return symbol, payload["bid"], payload["ask"]


def _worker_main(connection, engine, session, policy):
    """Pure observation worker. Messages never authorize an order or new code."""
    identity = {"engine": engine, "session": session, "policy": policy}
    symbols = OrderedDict()
    sequence = 0
    try:
        connection.send_bytes(_encode({**identity, "sequence": 0, "kind": "READY", "payload": {"pid": os.getpid()}}))
        while True:
            message = _decode(connection.recv_bytes(MAX_MESSAGE_BYTES))
            if set(message) != {*identity, "sequence", "kind", "payload"}:
                break
            if any(message[key] != value for key, value in identity.items()):
                break
            if type(message["sequence"]) is not int or message["sequence"] != sequence + 1:
                break
            sequence += 1
            kind = message["kind"]
            payload = message["payload"]
            if kind == "STOP" and payload == {}:
                break
            if kind == "PING" and payload == {}:
                result = {"pid": os.getpid(), "symbols": len(symbols)}
                reply = "PONG"
            elif kind == "OBSERVE":
                symbol, bid, ask = _quote(payload)
                samples = symbols.pop(symbol, 0) + 1
                symbols[symbol] = samples
                if len(symbols) > MAX_SYMBOLS:
                    symbols.popitem(last=False)
                result = {"symbol": symbol, "bid": bid, "ask": ask, "samples": samples}
                reply = "OBSERVED"
            else:
                break
            connection.send_bytes(_encode({**identity, "sequence": sequence, "kind": reply, "payload": result}))
    except (EOFError, OSError, ValueError, TypeError, RecursionError):
        pass
    finally:
        connection.close()


class EngineWorker:
    """Single-owner, caller-polled process handle with at most one in-flight request.

    Poll frequently to enforce deadlines. Failure never automatically restarts,
    retries, trades, or edits code. Use a new handle for an explicitly new session.
    """

    def __init__(self, engine: EngineId, *, policy_version: str, timeout_seconds: float = 2):
        self._engine = EngineId(engine)
        if not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 128:
            raise ValueError("A bounded immutable policy version is required")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0.05 <= timeout_seconds <= 30:
            raise ValueError("Invalid response deadline")
        self._policy_version = policy_version
        self.timeout_seconds = timeout_seconds
        self._session_id = uuid4().hex
        self.state = WorkerState.NEW
        self.failure_reason = None
        self._process = None
        self._connection = None
        self._sequence = 0
        self._expected = None
        self._expected_payload = None
        self._deadline = None
        self._owner_pid = os.getpid()
        self._owner_thread = threading.get_ident()

    @property
    def engine(self):
        return self._engine

    @property
    def policy_version(self):
        return self._policy_version

    @property
    def session_id(self):
        return self._session_id

    @property
    def pid(self):
        return self._process.pid if self._process else None

    def _owner(self):
        if os.getpid() != self._owner_pid or threading.get_ident() != self._owner_thread:
            raise RuntimeError("Worker handle belongs to a different process or thread")

    def start(self):
        self._owner()
        if self.state != WorkerState.NEW:
            raise RuntimeError("Worker already started or closed")
        context = mp.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=_worker_main,
                                  args=(child, self.engine.value, self.session_id, self.policy_version),
                                  name="atlas-observer-" + self.engine.value, daemon=True)
        self._connection = parent
        self._process = process
        try:
            process.start()
        except Exception:
            parent.close()
            child.close()
            self.state = WorkerState.FAILED
            self.failure_reason = "START_FAILED"
            raise
        child.close()
        self.state = WorkerState.STARTING
        self._expected = "READY"
        self._deadline = time.monotonic() + self.timeout_seconds

    def submit(self, kind: str, payload: dict | None = None):
        self._owner()
        if self.state != WorkerState.READY:
            raise RuntimeError("Worker is not ready; requests are not queued")
        payload = {} if payload is None else payload
        if kind == "OBSERVE":
            _quote(payload)
            expected = "OBSERVED"
        elif kind == "PING" and payload == {}:
            expected = "PONG"
        else:
            raise ValueError("Only observation and health requests are supported")
        next_sequence = self._sequence + 1
        data = _encode({"engine": self.engine.value, "session": self.session_id,
                        "policy": self.policy_version, "sequence": next_sequence,
                        "kind": kind, "payload": payload})
        try:
            self._connection.send_bytes(data)
        except (OSError, EOFError):
            self._fail("PIPE_CLOSED")
            return False
        self._sequence = next_sequence
        self._expected = expected
        self._expected_payload = dict(payload)
        self._deadline = time.monotonic() + self.timeout_seconds
        self.state = WorkerState.BUSY
        return True

    def poll(self):
        self._owner()
        if self.state not in {WorkerState.STARTING, WorkerState.BUSY, WorkerState.READY}:
            return None
        # A late reply cannot recover a timed-out request.
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._fail("RESPONSE_TIMEOUT")
            return None
        try:
            if self._connection.poll():
                reply = _decode(self._connection.recv_bytes(MAX_MESSAGE_BYTES))
                if (set(reply) != {"engine", "session", "policy", "sequence", "kind", "payload"}
                        or reply["engine"] != self.engine.value or reply["session"] != self.session_id
                        or reply["policy"] != self.policy_version
                        or type(reply["sequence"]) is not int or reply["sequence"] != self._sequence
                        or self._expected is None or reply["kind"] != self._expected
                        or not isinstance(reply["payload"], dict)):
                    self._fail("RESPONSE_IDENTITY_MISMATCH")
                    return None
                payload = reply["payload"]
                if self._expected == "READY":
                    valid = set(payload) == {"pid"} and type(payload["pid"]) is int and payload["pid"] == self.pid
                elif self._expected == "PONG":
                    valid = (set(payload) == {"pid", "symbols"} and type(payload["pid"]) is int
                             and payload["pid"] == self.pid and type(payload["symbols"]) is int
                             and 0 <= payload["symbols"] <= MAX_SYMBOLS)
                else:
                    valid = (set(payload) == {"symbol", "bid", "ask", "samples"}
                             and all(payload[key] == value for key, value in self._expected_payload.items())
                             and type(payload["samples"]) is int and payload["samples"] > 0)
                if not valid:
                    self._fail("INVALID_RESPONSE_PAYLOAD")
                    return None
                self._expected = None
                self._expected_payload = None
                self._deadline = None
                self.state = WorkerState.READY
                return reply
            if not self._process.is_alive():
                self._fail("WORKER_EXITED")
        except (EOFError, OSError, ValueError, TypeError, RecursionError):
            self._fail("INVALID_RESPONSE_OR_PIPE_CLOSED")
        return None

    def _dispose(self):
        if self._connection is not None:
            self._connection.close()
        if self._process is not None and self._process.pid is not None:
            self._process.join(timeout=0.2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=1)

    def _fail(self, reason):
        self.failure_reason = reason
        self.state = WorkerState.FAILED
        self._dispose()

    def close(self):
        self._owner()
        if self.state == WorkerState.CLOSED:
            return
        if self.state in {WorkerState.STARTING, WorkerState.READY, WorkerState.BUSY}:
            try:
                self._connection.send_bytes(_encode({"engine": self.engine.value, "session": self.session_id,
                    "policy": self.policy_version, "sequence": self._sequence + 1, "kind": "STOP", "payload": {}}))
            except (OSError, EOFError):
                pass
        self._dispose()
        self.state = WorkerState.CLOSED

    def status(self):
        return {"engine": self.engine.value, "session": self.session_id, "policy": self.policy_version,
                "state": self.state.value, "pid": self.pid, "failure_reason": self.failure_reason,
                "execution_enabled": False}
