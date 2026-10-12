"""Bounded options proposal subprocess; parent retains all execution authority."""
from datetime import timedelta
import json

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.contract_serialization import encode
from app.asset_modules.contract_worker_codec import _fields, _decimal, _time, _symbol
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import EngineWorker, WorkerState, _worker_main
from app.asset_modules.options_signals import (
    POLICY, OptionsHistory, OptionsPaperController, OptionsSignalSettings, propose,
)

MAX_BARS = 48
MAX_CONTRACTS = 8


def _quote(value):
    if value is None:
        return None
    _fields(value, 'symbol bid ask source_at observed_at')
    return Quote(_symbol(value['symbol']), _decimal(value['bid']), _decimal(value['ask']),
                 _time(value['source_at']), _time(value['observed_at']))


def _unpack(payload):
    _fields(payload, 'underlying bars underlying_quote contracts quotes at settings')
    underlying = payload['underlying']
    if underlying not in {'SPY', 'QQQ'}:
        raise ValueError('SPY/QQQ underlying required')
    raw = payload['bars']
    if not isinstance(raw, list) or len(raw) > MAX_BARS:
        raise ValueError('Bounded bar list required')
    bars = []
    for b in raw:
        _fields(b, 'end high low close')
        bars.append(Bar(_time(b['end']), _decimal(b['high']), _decimal(b['low']), _decimal(b['close'])))
    raw = payload['contracts']
    if not isinstance(raw, list) or len(raw) > MAX_CONTRACTS:
        raise ValueError('At most eight supplied contract alternatives required')
    contracts = []
    for c in raw:
        _fields(c, 'symbol expires_at multiplier tick margin underlying right strike')
        contracts.append(Contract(_symbol(c['symbol']), EngineId.OPTIONS, _time(c['expires_at']),
            _decimal(c['multiplier']), _decimal(c['tick']), _decimal(c['margin']),
            c['underlying'], c['right'], _decimal(c['strike'])))
    quotes = payload['quotes']
    if not isinstance(quotes, dict) or not set(quotes) <= {c.symbol for c in contracts}:
        raise ValueError('Only supplied-contract quotes permitted')
    quotes = {_symbol(s): _quote(q) for s,q in quotes.items()}
    if any(q is None or q.symbol != s for s,q in quotes.items()):
        raise ValueError('Quote key must match contract identity')
    s = _fields(payload['settings'], 'risk_budget capital_budget fee_per_side slippage stop_distance target_distance max_contracts hold_microseconds')
    hold = s['hold_microseconds']
    if type(hold) is not int or not 0 < hold <= 60_000_000:
        raise ValueError('At most sixty seconds hold required')
    settings = OptionsSignalSettings(*(_decimal(s[k]) for k in (
        'risk_budget','capital_budget','fee_per_side','slippage','stop_distance','target_distance')),
        s['max_contracts'], timedelta(microseconds=hold))
    return OptionsHistory(underlying,tuple(bars)), _quote(payload['underlying_quote']), tuple(contracts), quotes, _time(payload['at']), settings


def _pack_quote(q):
    if q is None:
        return None
    if not isinstance(q, Quote):
        raise ValueError('Typed quote required')
    return {'symbol':q.symbol,'bid':str(q.bid),'ask':str(q.ask),
            'source_at':q.source_at.isoformat(),'observed_at':q.observed_at.isoformat()}


def _pack(h, uq, contracts, quotes, now, settings):
    if not isinstance(h, OptionsHistory) or not isinstance(h.bars,tuple) or len(h.bars) > MAX_BARS:
        raise ValueError('At most 48 typed completed bars required')
    if any(not isinstance(b,Bar) for b in h.bars) or not isinstance(settings,OptionsSignalSettings):
        raise ValueError('Typed history and settings required')
    if (not isinstance(contracts,tuple) or len(contracts) > MAX_CONTRACTS
            or any(not isinstance(c,Contract) or c.engine is not EngineId.OPTIONS for c in contracts)):
        raise ValueError('At most eight typed option alternatives required')
    if not isinstance(quotes,dict) or not set(quotes) <= {c.symbol for c in contracts}:
        raise ValueError('Only supplied-contract quotes permitted')
    return {'underlying':h.underlying,
            'bars':[{'end':b.end.isoformat(),'high':str(b.high),'low':str(b.low),'close':str(b.close)} for b in h.bars],
            'underlying_quote':_pack_quote(uq),
            'contracts':[{'symbol':c.symbol,'expires_at':c.expires_at.isoformat(),
                'multiplier':str(c.multiplier),'tick':str(c.tick),'margin':str(c.margin),
                'underlying':c.underlying,'right':c.right,'strike':str(c.strike)} for c in contracts],
            'quotes':{s:_pack_quote(q) for s,q in quotes.items()}, 'at':now.isoformat(),
            'settings':{**{k:str(getattr(settings,k)) for k in (
                'risk_budget','capital_budget','fee_per_side','slippage','stop_distance','target_distance')},
                'max_contracts':settings.max_contracts,
                'hold_microseconds':settings.hold//timedelta(microseconds=1)}}


def _assess(payload):
    h,uq,cs,qs,now,settings = _unpack(payload)
    candidates,reason = propose(h,uq,cs,qs,now,settings)
    return {'reason':reason,'proposals':[{
        'intent_id':p.intent_id,'signal_at':p.signal_at.isoformat(),
        'underlying_direction':p.underlying_direction,'plan':json.loads(encode(p.plan))} for p in candidates]}


def _options_main(connection,engine,session,policy):
    _worker_main(connection,engine,session,policy,assessor=_assess)


class OptionsSignalWorker(EngineWorker):
    """Pure spawned evaluator, with no ledger path or order permissions."""
    _target = staticmethod(_options_main)

    def __init__(self, *, timeout_seconds=2):
        super().__init__(EngineId.OPTIONS, policy_version=POLICY, timeout_seconds=timeout_seconds)

    def assess(self, history, underlying_quote, contracts, quotes, now, settings):
        return self.submit('ASSESS',_pack(history,underlying_quote,contracts,quotes,now,settings))

    def _request(self,kind,payload):
        if kind == 'ASSESS':
            _assess(payload)
            return 'ASSESSED'
        return super()._request(kind,payload)

    def _valid_payload(self,payload):
        if self._expected == 'ASSESSED':
            return json.dumps(payload,sort_keys=True,allow_nan=False) == json.dumps(
                _assess(self._expected_payload),sort_keys=True,allow_nan=False)
        return super()._valid_payload(payload)


class OptionsWorkerController:
    """Caller-polled offline bridge; the parent revalidates before ledger entry."""
    def __init__(self,worker,controller):
        if not isinstance(worker,OptionsSignalWorker) or not isinstance(controller,OptionsPaperController):
            raise ValueError('Options worker and dedicated PAPER controller required')
        self.worker,self.controller,self.pending = worker,controller,None

    def submit(self,history,underlying_quote,contracts,quotes,*,now):
        self.worker._owner()
        protection = self.controller.protect(quotes,now=now)
        if self.worker.state in {WorkerState.FAILED,WorkerState.CLOSED}:
            self.controller.ledger.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        if self.pending is not None or self.worker.state != WorkerState.READY:
            return {'entry':'WORKER_NOT_READY','protection':protection}
        if not self.worker.assess(history,underlying_quote,contracts,quotes,now,self.controller.settings):
            self.controller.ledger.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        self.pending = (history,contracts,now,self.controller.settings)
        return {'entry':'PENDING_WORKER','protection':protection}

    def poll(self,underlying_quote,quotes,*,now):
        self.worker._owner()
        protection = self.controller.protect(quotes,now=now)
        reply = self.worker.poll()
        if self.worker.state in {WorkerState.FAILED,WorkerState.CLOSED}:
            self.controller.ledger.set_enabled(False)
            self.pending = None
            return {'entry':'WORKER_UNAVAILABLE','protection':protection}
        if not reply or reply['kind'] != 'ASSESSED':
            return {'entry':'PENDING_WORKER' if self.pending else 'IDLE','protection':protection}
        if self.pending is None:
            self.controller.ledger.set_enabled(False)
            return {'entry':'UNOWNED_COMPLETION','protection':protection}
        h,cs,submitted,settings = self.pending
        self.pending = None
        if not timedelta(0) <= now-submitted <= timedelta(seconds=2):
            return {'entry':'COMPLETION_EXPIRED','protection':protection}
        if self.controller.settings != settings:
            return {'entry':'POLICY_CHANGED','protection':protection}
        result = reply['payload']
        if not result['proposals']:
            return {'entry':result['reason'],'protection':protection}
        # Current parent quotes determine ranking, sizing, timestamps and execution.
        outcome = self.controller.step(h,underlying_quote,cs,quotes,now=now)
        outcome['protection'] = {**protection,**outcome['protection']}
        return outcome

    def close(self):
        self.worker._owner()
        self.controller.ledger.set_enabled(False)
        self.pending = None
        self.worker.close()
