"""Pure crypto proposal subprocess; shared allocation and exits stay in parent."""
from datetime import timedelta
import json

from app.asset_modules.contract_logic import Bar
from app.asset_modules.contract_worker_codec import _fields, _decimal, _time, _symbol
from app.asset_modules.crypto_signals import POLICY, CryptoHistory, CryptoQuote, CryptoSignalSettings, propose
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import EngineWorker, WorkerState, _worker_main


def _unpack(payload):
    _fields(payload, 'symbol bars quote at settings')
    symbol = _symbol(payload['symbol'])
    raw = payload['bars']
    if not isinstance(raw, list) or len(raw) > 48: raise ValueError('Bounded completed bars required')
    bars = []
    for bar in raw:
        _fields(bar, 'end high low close')
        bars.append(Bar(_time(bar['end']), *(_decimal(bar[k]) for k in ('high','low','close'))))
    q = payload['quote']
    if q is not None:
        _fields(q, 'symbol bid ask timestamp')
        q = CryptoQuote(_symbol(q['symbol']), _decimal(q['bid']), _decimal(q['ask']), _time(q['timestamp']))
    values = _fields(payload['settings'], ' '.join(CryptoSignalSettings.__dataclass_fields__))
    settings = CryptoSignalSettings(**{k:_decimal(v) for k,v in values.items()})
    return CryptoHistory(symbol, tuple(bars)), q, _time(payload['at']), settings


def _quote(row):
    if row is None: return None
    pair = getattr(row, 'pair', None)
    if pair is None or pair.quote_asset != 'USD': raise ValueError('Owned spot USD quote required')
    return CryptoQuote(pair.canonical_symbol, row.bid, row.ask, row.timestamp)


def _pack(history, quote, now, settings):
    if not isinstance(history, CryptoHistory) or not isinstance(settings, CryptoSignalSettings):
        raise ValueError('Typed crypto worker inputs required')
    if not isinstance(history.bars, tuple) or len(history.bars) > 48: raise ValueError('Bounded history required')
    if any(not isinstance(bar, Bar) for bar in history.bars): raise ValueError('Typed completed bars required')
    q = _quote(quote)
    return {'symbol':history.symbol, 'bars':[{'end':b.end.isoformat(),'high':str(b.high),
        'low':str(b.low),'close':str(b.close)} for b in history.bars],
        'quote':None if q is None else {'symbol':q.symbol,'bid':str(q.bid),'ask':str(q.ask),'timestamp':q.timestamp.isoformat()},
        'at':now.isoformat(), 'settings':{k:str(getattr(settings,k)) for k in settings.__dataclass_fields__}}


def _assess(payload):
    p, reason = propose(*_unpack(payload))
    return {'proposal':p, 'reason':reason}


def _crypto_main(connection, engine, session, policy):
    _worker_main(connection, engine, session, policy, assessor=_assess)


class CryptoSignalWorker(EngineWorker):
    _target = staticmethod(_crypto_main)

    def __init__(self, *, timeout_seconds=2):
        super().__init__(EngineId.CRYPTO, policy_version=POLICY, timeout_seconds=timeout_seconds)

    def assess(self, history, quote, now, settings):
        return self.submit('ASSESS', _pack(history, quote, now, settings))

    def _request(self, kind, payload):
        if kind == 'ASSESS':
            _assess(payload)
            return 'ASSESSED'
        return super()._request(kind, payload)

    def _valid_payload(self, payload):
        if self._expected == 'ASSESSED':
            return json.dumps(payload, sort_keys=True, allow_nan=False) == json.dumps(
                _assess(self._expected_payload), sort_keys=True, allow_nan=False)
        return super()._valid_payload(payload)


class CryptoWorkerController:
    """Caller-polled offline handoff. No automatic desktop activation or retries."""
    def __init__(self, worker, router, *, settings):
        from app.asset_modules.crypto_allocation import CryptoAllocationRouter
        if (not isinstance(worker, CryptoSignalWorker) or not isinstance(router, CryptoAllocationRouter)
                or not isinstance(settings, CryptoSignalSettings)):
            raise ValueError('Crypto worker, shared router and settings required')
        if settings.fee != router.ledger.fee or settings.slippage != router.ledger.slippage:
            raise ValueError('Signal costs must match the ledger')
        self.worker, self.router, self.settings, self.pending = worker, router, settings, None
        router.set_enabled(False)

    def submit(self, history, quotes, *, now):
        self.worker._owner()
        protection = self.router.protect(quotes, now=now)
        if self.worker.state in {WorkerState.FAILED, WorkerState.CLOSED}:
            self.router.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE', **protection}
        if self.pending is not None or self.worker.state != WorkerState.READY:
            return {'entry':'WORKER_NOT_READY', **protection}
        if not self.worker.assess(history, quotes.get(history.symbol), now, self.settings):
            self.router.set_enabled(False)
            return {'entry':'WORKER_UNAVAILABLE', **protection}
        self.pending = (history, now, self.settings)
        return {'entry':'PENDING_WORKER', **protection}

    def poll(self, quotes, *, now):
        self.worker._owner()
        protection = self.router.protect(quotes, now=now)
        reply = self.worker.poll()
        if self.worker.state in {WorkerState.FAILED, WorkerState.CLOSED}:
            self.router.set_enabled(False)
            self.pending = None
            return {'entry':'WORKER_UNAVAILABLE', **protection}
        if not reply or reply['kind'] != 'ASSESSED':
            return {'entry':'PENDING_WORKER' if self.pending else 'IDLE', **protection}
        if self.pending is None:
            self.router.set_enabled(False)
            return {'entry':'UNOWNED_COMPLETION', **protection}
        history, submitted, settings = self.pending
        self.pending = None
        if not timedelta(0) <= now-submitted <= timedelta(seconds=2):
            return {'entry':'COMPLETION_EXPIRED', **protection}
        if settings != self.settings:
            return {'entry':'POLICY_CHANGED', **protection}
        if settings.fee != self.router.ledger.fee or settings.slippage != self.router.ledger.slippage:
            self.router.set_enabled(False)
            return {'entry':'COST_MODEL_CHANGED', **protection}
        if reply['payload']['proposal'] is None:
            return {'entry':reply['payload']['reason'], **protection}
        # Recompute all prices and budgets in the parent with current quotes.
        p, reason = propose(history, _quote(quotes.get(history.symbol)), now, settings)
        if p is None: return {'entry':reason, **protection}
        return self.router.submit(p, quotes, now=now)

    def close(self):
        self.worker._owner()
        self.router.set_enabled(False)
        self.pending = None
        self.worker.close()
