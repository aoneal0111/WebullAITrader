"""Bounded futures proposal subprocess and offline parent-ledger handoff."""
from datetime import datetime, timedelta
import json

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.contract_serialization import encode
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import EngineWorker, WorkerState, _worker_main
from app.asset_modules.futures_signals import (
    POLICY, FuturesHistory, FuturesPaperController, FuturesSignalSettings, propose,
)

from app.asset_modules.contract_worker_codec import _fields, _decimal, _time, _symbol

MAX_BARS = 48


def _unpack(payload):
    _fields(payload, 'contract bars quote at settings')
    c = _fields(payload['contract'], 'symbol expires_at multiplier tick margin')
    c = Contract(_symbol(c['symbol']), EngineId.FUTURES, _time(c['expires_at']),
                 _decimal(c['multiplier']), _decimal(c['tick']), _decimal(c['margin']))
    raw = payload['bars']
    if not isinstance(raw, list) or len(raw) > MAX_BARS:
        raise ValueError('Bounded bar list required')
    bars = []
    for b in raw:
        _fields(b, 'end high low close')
        bars.append(Bar(_time(b['end']), _decimal(b['high']), _decimal(b['low']), _decimal(b['close'])))
    q = payload['quote']
    if q is not None:
        _fields(q, 'symbol bid ask source_at observed_at')
        q = Quote(_symbol(q['symbol']), _decimal(q['bid']), _decimal(q['ask']),
                  _time(q['source_at']), _time(q['observed_at']))
    s = _fields(payload['settings'], 'risk_budget capital_budget fee_per_side max_extension_r target_r slippage_ticks max_contracts hold_microseconds')
    hold = s['hold_microseconds']
    if type(hold) is not int or not 0 < hold <= 3_600_000_000:
        raise ValueError('Bounded hold duration required')
    settings = FuturesSignalSettings(*(_decimal(s[k]) for k in (
        'risk_budget','capital_budget','fee_per_side','max_extension_r','target_r')),
        s['slippage_ticks'], s['max_contracts'], timedelta(microseconds=hold))
    return c, FuturesHistory(c.symbol, tuple(bars)), q, _time(payload['at']), settings


def _pack(c, h, q, now, settings):
    if not isinstance(c, Contract) or not isinstance(settings, FuturesSignalSettings):
        raise ValueError('Typed contract and signal settings required')
    if not isinstance(h, FuturesHistory) or h.symbol != c.symbol or not isinstance(h.bars, tuple):
        raise ValueError('Owned typed contract history required')
    if c.engine is not EngineId.FUTURES or any((c.underlying, c.right, c.strike is not None)):
        raise ValueError('Actual futures contract, without option fields, required')
    if len(h.bars) > MAX_BARS:
        raise ValueError('Worker supports at most 48 completed bars per request')
    if any(not isinstance(b, Bar) for b in h.bars) or q is not None and not isinstance(q, Quote):
        raise ValueError('Typed bars and quote required')
    return {'contract':{'symbol':c.symbol,'expires_at':c.expires_at.isoformat(),
                        'multiplier':str(c.multiplier),'tick':str(c.tick),'margin':str(c.margin)},
            'bars':[{'end':b.end.isoformat(),'high':str(b.high),'low':str(b.low),'close':str(b.close)} for b in h.bars],
            'quote':None if q is None else {'symbol':q.symbol,'bid':str(q.bid),'ask':str(q.ask),
                                           'source_at':q.source_at.isoformat(),'observed_at':q.observed_at.isoformat()},
            'at':now.isoformat(),
            'settings':{**{k:str(getattr(settings,k)) for k in (
                'risk_budget','capital_budget','fee_per_side','max_extension_r','target_r')},
                'slippage_ticks':settings.slippage_ticks,'max_contracts':settings.max_contracts,
                'hold_microseconds':settings.hold//timedelta(microseconds=1)}}


def _assess(payload):
    c,h,q,now,settings = _unpack(payload)
    candidate, reason = propose(c,h,q,now,settings)
    return {'reason':reason,'proposal':None if candidate is None else {
        'intent_id':candidate.intent_id,'signal_at':candidate.signal_at.isoformat(),
        'trigger':str(candidate.trigger),'plan':json.loads(encode(candidate.plan))}}


def _futures_main(connection, engine, session, policy):
    _worker_main(connection,engine,session,policy,assessor=_assess)


class FuturesSignalWorker(EngineWorker):
    """Pure evaluator. It never receives a ledger path or execution permission."""
    _target = staticmethod(_futures_main)

    def __init__(self, *, timeout_seconds=2):
        super().__init__(EngineId.FUTURES, policy_version=POLICY, timeout_seconds=timeout_seconds)

    def assess(self, contract, history, quote, now, settings):
        return self.submit('ASSESS', _pack(contract,history,quote,now,settings))

    def _request(self, kind, payload):
        if kind == 'ASSESS':
            _assess(payload)  # Validate before sending, while the handle remains READY.
            return 'ASSESSED'
        return super()._request(kind,payload)

    def _valid_payload(self, payload):
        if self._expected == 'ASSESSED':
            # A worker message cannot invent a plan: verify the entire response.
            return json.dumps(payload,sort_keys=True,allow_nan=False) == json.dumps(
                _assess(self._expected_payload),sort_keys=True,allow_nan=False)
        return super()._valid_payload(payload)


class FuturesWorkerController:
    """Caller-polled offline bridge; parent owns all ledger/protection authority."""
    def __init__(self, worker, controller):
        if not isinstance(worker, FuturesSignalWorker) or not isinstance(controller, FuturesPaperController):
            raise ValueError('Futures worker and dedicated PAPER controller required')
        self.worker, self.controller, self.pending = worker, controller, None

    def submit(self, contract, history, quotes, *, now):
        self.worker._owner()
        protection = self.controller.protect(quotes,now=now)
        if self.worker.state in {WorkerState.FAILED, WorkerState.CLOSED}:
            self.controller.ledger.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        if self.pending is not None or self.worker.state != WorkerState.READY:
            return {'entry':'WORKER_NOT_READY','protection':protection}
        if not self.worker.assess(contract,history,quotes.get(contract.symbol),now,self.controller.settings):
            self.controller.ledger.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        self.pending = (contract,history,now,self.controller.settings)
        return {'entry':'PENDING_WORKER','protection':protection}

    def poll(self, quotes, *, now):
        self.worker._owner()
        protection = self.controller.protect(quotes,now=now)
        reply = self.worker.poll()
        if self.worker.state in {WorkerState.FAILED, WorkerState.CLOSED}:
            self.controller.ledger.set_enabled(False)
            self.pending = None
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        if not reply or reply['kind'] != 'ASSESSED':
            return {'entry':'PENDING_WORKER' if self.pending else 'IDLE','protection':protection}
        if self.pending is None:
            self.controller.ledger.set_enabled(False)
            return {'entry':'UNOWNED_COMPLETION','protection':protection}
        c,h,submitted,settings = self.pending
        self.pending = None
        if not timedelta(0) <= now-submitted <= timedelta(seconds=2):
            return {'entry':'COMPLETION_EXPIRED','protection':protection}
        if self.controller.settings != settings:
            return {'entry':'POLICY_CHANGED','protection':protection}
        result = reply['payload']
        if result['proposal'] is None:
            return {'entry':result['reason'],'protection':protection}
        # Recompute against current quotes and authorize in the parent. Never dispatch
        # the child's old prices, timestamp, sizing, or serialized plan directly.
        outcome = self.controller.step(c,h,quotes,now=now)
        outcome['protection'] = {**protection, **outcome['protection']}
        return outcome

    def close(self):
        self.worker._owner()
        self.controller.ledger.set_enabled(False)
        self.pending = None
        self.worker.close()
