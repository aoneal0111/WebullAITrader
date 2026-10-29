"""Explicit bounded native PAPER experiment; never a release promotion."""
from dataclasses import replace
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
import os
import re
from pathlib import Path
import sqlite3
from threading import RLock
from time import monotonic
from uuid import uuid4

from .admission import EquityQualificationPolicy

ENV='ATLAS_EQUITY_PAPER_EXPERIMENT'
FAMILIES={'WARRIOR_MOMENTUM':('FIRST_PULLBACK','HOD_RECLAIM','HIGH_OF_DAY_BREAKOUT','BREAKOUT_RETEST_CONTINUATION','OPENING_RANGE_BREAKOUT'),
          'QUICK_SCALPER':('QUICK_SCALP_STREAM','QUICK_SCALP_MOMENTUM')}
LIMITS=dict(hours=8,maximum_entry_attempts=50,maximum_quantity=10,maximum_position_dollars='100',
            maximum_initial_risk='1',maximum_total_entry_notional='5000')


def create(directory,clock=lambda:datetime.now(UTC)):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    now=clock();spec=dict(scope='NATIVE_PAPER_EXPERIMENT_V1',trial_id=uuid4().hex,
        created_at=now.isoformat(),expires_at=(now+timedelta(hours=8)).isoformat(),limits=LIMITS,families=FAMILIES)
    with (root/'experiment.json').open('x',encoding='utf-8') as f:json.dump(spec,f,indent=2)
    return spec


def load(path,clock=lambda:datetime.now(UTC)):
    path=Path(path).resolve()
    if path.stat().st_size>16384:raise ValueError('EXPERIMENT_SPEC_BOUND')
    spec=json.loads(path.read_text(encoding='utf-8'))
    if (spec.get('scope')!='NATIVE_PAPER_EXPERIMENT_V1' or spec.get('limits')!=LIMITS
        or spec.get('families')!={k:list(v) for k,v in FAMILIES.items()}
        or len(spec.get('trial_id',''))!=32):raise ValueError('EXPERIMENT_SPEC_INVALID')
    start,end=(datetime.fromisoformat(spec[k]) for k in ('created_at','expires_at'))
    if start.utcoffset() is None or end.utcoffset() is None or end-start!=timedelta(hours=8) or not start<=clock()<end:
        raise ValueError('EXPERIMENT_EXPIRED_OR_FUTURE')
    return path,spec


def configured(configuration,runtime_mode):
    value=os.environ.get(ENV,'').strip()
    if not value:return None
    if (runtime_mode!='PAPER' or configuration.environment.value!='PAPER' or configuration.live_trading_enabled
        or configuration.grok_paper_entries_enabled or not configuration.daily_research_enabled):
        raise ValueError('EXPERIMENT_REQUIRES_PAPER_LIVE_OFF_GROK_ORDERS_OFF_AND_CAPTURE')
    path,spec=load(value)
    return PaperExperiment(path,spec)


class PaperExperiment(EquityQualificationPolicy):
    def __init__(self,path,spec,clock=lambda:datetime.now(UTC),timer=monotonic):
        super().__init__();self.path=Path(path);self.spec=spec;self.clock=clock;self.timer=timer
        self._started=timer();self._wall_start=clock();self._bindings={};self._lock=RLock();self.capture_ready=None
        self.db_path=self.path.with_name('entry-attempts.sqlite3')
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (trial TEXT,request TEXT,notional TEXT,record TEXT,PRIMARY KEY(trial,request))');db.commit()

    def active(self):
        try:
            # Removal/revocation/expiry all stop new entries; never affect SELLs.
            path,spec=load(self.path,self.clock)
            elapsed=self.timer()-self._started
            wall=(self.clock()-self._wall_start).total_seconds()
            return bool(self.capture_ready and self.capture_ready() and not self.path.with_name('PAUSE_ENTRIES').exists() and spec==self.spec and 0<=elapsed<8*3600 and abs(wall-elapsed)<=2)
        except Exception:return False

    def bind(self,attribution):
        self._bindings={owner:(attribution._code[engine],attribution._configuration[engine]) for owner,engine in
            (('WARRIOR_MOMENTUM','WARRIOR_MOMENTUM_V1'),('QUICK_SCALPER','QUICK_SCALPER'))}
        if any(not re.fullmatch('[0-9a-f]{64}',v) for values in self._bindings.values() for v in values):
            self._bindings={};raise ValueError('EXPERIMENT_SOURCE_CONFIGURATION_FINGERPRINT_REQUIRED')

    def permits(self,metadata):
        if not isinstance(metadata,dict) or not self.active():return False
        owner=metadata.get('strategy_owner');binding=self._bindings.get(owner)
        return bool(binding and metadata.get('strategy_trial_id')==self.spec['trial_id']
            and metadata.get('strategy_family') in FAMILIES.get(owner,())
            and binding==(metadata.get('strategy_code_fingerprint'),metadata.get('strategy_configuration_fingerprint')))

    def claim_order(self,placement):
        order=placement.order;metadata=dict(order.metadata)
        if not self.permits(metadata) or metadata.get('source')!='autonomous-paper' or metadata.get('reason')!='ENTRY':return False
        try:
            qty=order.quantity;price=order.limit_price;stop=D(str(metadata.get('structural_stop','')))
            risk=D(str(metadata.get('risk_dollars','')))
            if (order.order_type.value!='LIMIT' or price is None or any(not v.is_finite() for v in (qty,price,stop,risk))
                or not 0<qty<=10 or qty!=qty.to_integral_value() or not 0<stop<price
                or qty*price>100 or not 0<risk<=1 or qty*(price-stop)>1):return False
            notional=qty*price
            record=dict(trial_id=self.spec['trial_id'],request_id=order.request_id,symbol=order.symbol,
                strategy_owner=metadata['strategy_owner'],strategy_family=metadata['strategy_family'],
                quantity=str(qty),limit_price=str(price),structural_stop=str(stop),initial_risk=str(risk),
                claimed_at=self.clock().isoformat(),state='RESERVED_BEFORE_GATEWAY_RESULT')
            with self._lock, closing(sqlite3.connect(self.db_path,timeout=2)) as db:
                db.execute('BEGIN IMMEDIATE')
                rows=db.execute('SELECT request,notional FROM attempts WHERE trial=?',(self.spec['trial_id'],)).fetchall()
                if len(rows)>=50 or any(row[0]==order.request_id for row in rows) or sum((D(row[1]) for row in rows),D(0))+notional>5000:
                    db.rollback();return False
                if not self.active():db.rollback();return False
                db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(self.spec['trial_id'],order.request_id,str(notional),json.dumps(record)))
                db.commit()
            return True
        except Exception:return False

    def constrain_placement(self,placement):
        metadata=dict(placement.order.metadata)
        deadline=min(datetime.fromisoformat(self.spec['expires_at']),self.clock()+timedelta(seconds=60))
        for key in ('chase_deadline','opportunity_deadline'):
            if metadata.get(key):
                value=datetime.fromisoformat(str(metadata[key]))
                if value.utcoffset() is None:raise ValueError('NAIVE_ENTRY_DEADLINE')
                deadline=min(deadline,value)
        if metadata.get('entry_validity_seconds') is not None:
            deadline=min(deadline,self.clock()+timedelta(seconds=float(metadata['entry_validity_seconds'])))
        metadata['chase_deadline']=deadline.isoformat()
        return replace(placement,order=replace(placement.order,metadata=metadata))

    def notice(self):
        state='entry trial enabled, subject to native checks' if self.active() else 'new entries paused, expired or capture unavailable'
        return ('EXPERIMENTAL PAPER: '+state+'. Max 10 shares / $100 position / $1 modeled initial risk; '
                '50 entry attempts; expires '+self.spec['expires_at']+'. Grok orders OFF. Position protection remains available.')

    def status(self):
        return dict(policy='NATIVE_PAPER_EXPERIMENT_V1',entry_mode='BOUNDED_PAPER_EXPERIMENT',approved_policies=[],
            trial_id=self.spec['trial_id'],expires_at=self.spec['expires_at'],limits=LIMITS,families=FAMILIES,
            protective_sells='AVAILABLE',reason='EXPERIMENTAL_NOT_RELEASE_QUALIFIED')


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--create',required=True)
    args=p.parse_args();root=Path(args.create)
    spec=load(root/'experiment.json')[1] if (root/'experiment.json').exists() else create(root)
    print('PAPER_EXPERIMENT_ID='+spec['trial_id']);print('EXPERIMENT_EXPIRES_AT='+spec['expires_at'])


if __name__=='__main__':main()
