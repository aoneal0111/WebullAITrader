"""Typed completed-bar evidence for disconnected Warrior/Scalper observation."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json

from .engine_catalog import EngineId
from .grok_observer import Snapshot, aware


ROLE_FOCUS = {
    EngineId.WARRIOR: 'Observe momentum structure, local pullbacks and extension in the supplied completed bars. '
        'Do not infer the full session high, day gain, catalyst or scanner ranking from this short window.',
    EngineId.SCALPER: 'Observe short-duration local support, nearby range and immediate price structure. '
        'Keep Quick Scalper separate from Warrior; do not substitute a momentum holding plan.',
}


def _number(value, *, zero=False):
    if (not isinstance(value, Decimal) or not value.is_finite() or value < 0
            or (not zero and value == 0) or len(value.as_tuple().digits)>24
            or value.adjusted()>12 or value.as_tuple().exponent < -8):
        raise ValueError('Bounded finite decimal evidence required')


@dataclass(frozen=True)
class ContextBar:
    closed_at: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        aware(self.closed_at); aware(self.available_at)
        if self.closed_at.second or self.closed_at.microsecond or self.available_at < self.closed_at:
            raise ValueError('Completed minute boundaries and causal availability required')
        for name in ('open','high','low','close'):
            _number(getattr(self,name))
        _number(self.volume,zero=True)
        if not self.low <= min(self.open,self.close) <= max(self.open,self.close) <= self.high:
            raise ValueError('Consistent OHLC evidence required')


@dataclass(frozen=True)
class StrategyContext:
    engine: EngineId
    event_id: str
    symbol: str
    baseline_sha256: str
    captured_at: datetime
    source: str
    setup_state: str
    baseline_result: str
    bars: tuple[ContextBar, ...]

    def payload(self, snapshot):
        if not isinstance(snapshot,Snapshot):
            raise ValueError('Typed quote snapshot required')
        if not isinstance(self.engine,EngineId) or self.engine not in ROLE_FOCUS:
            raise ValueError('Explicit Warrior or Scalper owner required')
        for name in ('engine','event_id','symbol','baseline_sha256','captured_at','source'):
            if getattr(self,name) != getattr(snapshot,name):
                raise ValueError('Context and snapshot identity mismatch')
        if self.setup_state not in {'UNKNOWN','FORMING','TRIGGERED','INVALIDATED'}:
            raise ValueError('Bounded setup state required')
        if self.baseline_result not in {'NO_ENTRY','WAIT','BLOCKED','ENTRY_CANDIDATE'}:
            raise ValueError('Recorded baseline assessment required')
        if not isinstance(self.bars,tuple) or not 5<=len(self.bars)<=30:
            raise ValueError('Five to thirty immutable completed bars required')
        previous=None
        for bar in self.bars:
            if not isinstance(bar,ContextBar) or bar.available_at>self.captured_at:
                raise ValueError('Typed bars available by capture required')
            if previous is not None and bar.closed_at-previous != timedelta(minutes=1):
                raise ValueError('Ordered contiguous completed minute bars required')
            previous=bar.closed_at
        if not timedelta(0)<=self.captured_at-previous<=timedelta(minutes=1):
            raise ValueError('Current completed history required')
        if min(snapshot.bid_at,snapshot.ask_at)<previous:
            raise ValueError('Quote must follow latest completed bar')
        payload=dict(engine=self.engine.value,event_id=self.event_id,symbol=self.symbol,
            baseline_sha256=self.baseline_sha256,captured_at=self.captured_at.isoformat(),source=self.source,
            interval='M1',setup_state=self.setup_state,baseline_result=self.baseline_result,
            role_focus=ROLE_FOCUS[self.engine],bars=[{k:v.isoformat() if isinstance(v,datetime) else str(v)
                for k,v in asdict(bar).items()} for bar in self.bars],
            evidence_limits=['Short completed-bar window only; no full-session, news, account or fill evidence',
                             'Baseline result is caller-supplied evidence, not authorization or verified performance'])
        if len(json.dumps(payload).encode())>16000:
            raise ValueError('Bounded strategy context required')
        return payload


def offline_check():
    """Real observer serializer, typed evidence and fake transport; zero API calls."""
    from datetime import UTC
    from decimal import Decimal as D
    from .grok import GrokClient
    from .grok_observer import GrokObserverTrial
    now=datetime(2026,10,24,14,tzinfo=UTC)
    baseline=sha256(b'ATLAS_CONTEXT_FIXTURE_BASELINE_ONLY').hexdigest()
    payloads=[]
    def transport(request):
        payloads.append(json.loads(request.data))
        answer=json.dumps(dict(view='PASS',reason='Synthetic observer evidence only; no execution authority.'))
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=answer)])])).encode()
    client=GrokClient('offline-fixture-not-a-credential','grok-4.7',transport=transport,clock=lambda:0)
    trial=GrokObserverTrial(client,baseline,clock=lambda:now)
    records=[]
    for engine in (EngineId.WARRIOR,EngineId.SCALPER):
        snapshot=Snapshot(engine,'context-'+engine.name,'AAPL',D(100),D('100.01'),now,now,now,
                          baseline,'RECORDED_FIXTURE')
        bars=tuple(ContextBar(now-timedelta(minutes=4-i),now-timedelta(minutes=4-i),
            D(100),D('100.1'),D('99.9'),D(100),D(1000)) for i in range(5))
        context=StrategyContext(engine,snapshot.event_id,'AAPL',baseline,now,'RECORDED_FIXTURE',
                                'FORMING','NO_ENTRY',bars)
        record=trial.observe(snapshot,strategy_context=context)
        if record.status!='OBSERVED' or record.execution_authorized:
            raise RuntimeError('Offline context fixture failed')
        records.append(asdict(record))
    if any(p['store'] is not False or 'tools' in p for p in payloads):
        raise RuntimeError('Unexpected fixture transport permissions')
    return dict(scope='OFFLINE_GROK_STRATEGY_CONTEXT_FIXTURES',network_requests=0,
        simulated_provider_requests=len(payloads),orders=0,baseline_modified=False,
        desktop_wiring=False,online_credentials_validated=False,records=records)


if __name__=='__main__':
    # Use canonical module identities for strict typed evidence checks under -m.
    from app.asset_modules.grok_observer_context import offline_check as run_fixture
    print(json.dumps(run_fixture(),indent=2))
