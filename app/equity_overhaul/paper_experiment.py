"""Explicit bounded native PAPER experiment; never a release promotion."""
from app.asset_modules.paper_profiles import GROK_CONTROL_PROFILES, ACCOUNT_SIZING_PROFILE, PAPER_PROFILES
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
ACCOUNT_LIMITS={k:(v if k=='hours' else None) for k,v in LIMITS.items()}
V2_FAMILIES={'WARRIOR_MOMENTUM':('MICRO_PULLBACK','MOMENTUM_REACCELERATION','RECLAIM_CONTINUATION'),
             'QUICK_SCALPER':FAMILIES['QUICK_SCALPER']}
V2_CENT_POLICY=dict(minimum='0.05',maximum='0.10',minimum_net_reward_r='1')
CLOCK_POLICY=dict(maximum_future_ms=250,maximum_book_age_ms=5000,provider_timestamps_modified=False)
PROFILES=PAPER_PROFILES
CONTINUOUS_SCOPE='NATIVE_PAPER_OPERATION_V1'
CONTINUOUS_POLICY=dict(scheduler='FAIR_WATCHLIST',batch_size=4,minimum_batch_seconds=10,minimum_symbol_seconds=60)
EXTENDED_ENTRY_SESSIONS=frozenset({'PREMARKET','REGULAR','AFTER_HOURS'})
WARRIOR_LIBRARY_POLICY='IMPLEMENTED_TAXONOMY_PAPER_V1'


def warrior_library_families():
    from app.opportunity_discovery.taxonomy import STRATEGY_TAXONOMY
    from app.opportunity_discovery.contracts import DetectorAvailability
    from app.strategies.warrior_momentum.models import SetupType
    return sorted({item.strategy_id for item in STRATEGY_TAXONOMY
                   if item.availability is DetectorAvailability.ACTIVE}
                  | {item.value for item in SetupType})



def create(directory,clock=lambda:datetime.now(UTC),*,profile='LEGACY_V1',extended_sessions=False,clock_tolerance=False,hours=8,continuous=False):
    if profile not in PROFILES:raise ValueError('UNKNOWN_EXPERIMENT_PROFILE')
    if type(clock_tolerance) is not bool or clock_tolerance and profile!=ACCOUNT_SIZING_PROFILE:
        raise ValueError('EXPLICIT_ACCOUNT_PAPER_CLOCK_PROFILE_REQUIRED')
    if type(extended_sessions) is not bool or extended_sessions and profile not in GROK_CONTROL_PROFILES:
        raise ValueError('EXPLICIT_GROK_PAPER_EXTENDED_SESSIONS_REQUIRED')
    if type(hours) is not int or hours not in (8,24) or (hours!=8 and profile!=ACCOUNT_SIZING_PROFILE):
        raise ValueError('EXPLICIT_ACCOUNT_PAPER_DURATION_REQUIRED')
    if type(continuous) is not bool or continuous and profile!=ACCOUNT_SIZING_PROFILE:
        raise ValueError('EXPLICIT_ACCOUNT_PAPER_OPERATION_REQUIRED')
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    now=clock();spec=dict(scope='NATIVE_PAPER_EXPERIMENT_V1',trial_id=uuid4().hex,
        created_at=now.isoformat(),expires_at=(now+timedelta(hours=hours)).isoformat(),limits=dict(LIMITS),families=FAMILIES)
    if profile in PROFILES[1:]:
        spec.update(scope=('NATIVE_PAPER_EXPERIMENT_V5' if profile in GROK_CONTROL_PROFILES else 'NATIVE_PAPER_EXPERIMENT_V4' if profile=='CENT_DEFENSE_V4' else 'NATIVE_PAPER_EXPERIMENT_V3' if profile=='CENT_EXECUTABLE_V3' else 'NATIVE_PAPER_EXPERIMENT_V2'),profile=profile,families=V2_FAMILIES,cent_policy=V2_CENT_POLICY)
    if profile in GROK_CONTROL_PROFILES:
        spec['families']={**V2_FAMILIES,'WARRIOR_MOMENTUM':(*V2_FAMILIES['WARRIOR_MOMENTUM'],'GROK_DIRECTED')}
    if profile==ACCOUNT_SIZING_PROFILE:spec.update(scope='NATIVE_PAPER_EXPERIMENT_V6',limits=dict(ACCOUNT_LIMITS))
    spec['limits']['hours']=hours
    if extended_sessions:spec['entry_sessions']=sorted(EXTENDED_ENTRY_SESSIONS)
    if clock_tolerance:spec['scalper_clock_policy']=dict(CLOCK_POLICY)
    if continuous:
        spec.update(scope=CONTINUOUS_SCOPE,operating_mode='CONTINUOUS_PAPER',expires_at=None,
            limits={k:None for k in LIMITS},grok_policy=dict(CONTINUOUS_POLICY))
    with (root/'experiment.json').open('x',encoding='utf-8') as f:json.dump(spec,f,indent=2)
    return spec


def load(path,clock=lambda:datetime.now(UTC),*,allow_expired=False):
    path=Path(path).resolve()
    if path.stat().st_size>16384:raise ValueError('EXPERIMENT_SPEC_BOUND')
    spec=json.loads(path.read_text(encoding='utf-8'))
    if spec.get('scope')==CONTINUOUS_SCOPE:
        validate_continuous(spec,clock())
        return path,spec
    if 'entry_sessions' in spec and (spec.get('profile') not in GROK_CONTROL_PROFILES
            or spec['entry_sessions']!=sorted(EXTENDED_ENTRY_SESSIONS)):
        raise ValueError('EXPERIMENT_SESSION_POLICY_INVALID')
    if 'scalper_clock_policy' in spec and (spec.get('profile')!=ACCOUNT_SIZING_PROFILE
            or spec['scalper_clock_policy']!=CLOCK_POLICY):
        raise ValueError('EXPERIMENT_CLOCK_POLICY_INVALID')
    v2=spec.get('scope') in {'NATIVE_PAPER_EXPERIMENT_V2','NATIVE_PAPER_EXPERIMENT_V3','NATIVE_PAPER_EXPERIMENT_V4','NATIVE_PAPER_EXPERIMENT_V5','NATIVE_PAPER_EXPERIMENT_V6'}
    expected_profile=ACCOUNT_SIZING_PROFILE if spec.get('scope')=='NATIVE_PAPER_EXPERIMENT_V6' else 'GROK_WARRIOR_V5' if spec.get('scope')=='NATIVE_PAPER_EXPERIMENT_V5' else 'CENT_DEFENSE_V4' if spec.get('scope')=='NATIVE_PAPER_EXPERIMENT_V4' else 'CENT_EXECUTABLE_V3' if spec.get('scope')=='NATIVE_PAPER_EXPERIMENT_V3' else 'CENT_EARLY_V2'
    families=({**V2_FAMILIES,'WARRIOR_MOMENTUM':(*V2_FAMILIES['WARRIOR_MOMENTUM'],'GROK_DIRECTED')}
              if expected_profile in GROK_CONTROL_PROFILES else V2_FAMILIES if v2 else FAMILIES)
    duration=spec.get('limits',{}).get('hours')
    expected_limits=dict(ACCOUNT_LIMITS if expected_profile==ACCOUNT_SIZING_PROFILE else LIMITS)
    if type(duration) is not int or duration not in ((8,24) if expected_profile==ACCOUNT_SIZING_PROFILE else (8,)):
        raise ValueError('EXPERIMENT_DURATION_INVALID')
    expected_limits['hours']=duration
    if (spec.get('scope') not in {'NATIVE_PAPER_EXPERIMENT_V1','NATIVE_PAPER_EXPERIMENT_V2','NATIVE_PAPER_EXPERIMENT_V3','NATIVE_PAPER_EXPERIMENT_V4','NATIVE_PAPER_EXPERIMENT_V5','NATIVE_PAPER_EXPERIMENT_V6'}
        or (v2 and (spec.get('profile')!=expected_profile or spec.get('cent_policy')!=V2_CENT_POLICY))
        or (not v2 and (spec.get('profile','LEGACY_V1')!='LEGACY_V1' or 'cent_policy' in spec))
        or spec.get('limits')!=expected_limits
        or spec.get('families')!={k:list(v) for k,v in families.items()}
        or len(spec.get('trial_id',''))!=32):raise ValueError('EXPERIMENT_SPEC_INVALID')
    start,end=(datetime.fromisoformat(spec[k]) for k in ('created_at','expires_at'))
    now=clock()
    if start.utcoffset() is None or end.utcoffset() is None or end-start!=timedelta(hours=duration) or now<start or (not allow_expired and now>=end):
        raise ValueError('EXPERIMENT_EXPIRED_OR_FUTURE')
    return path,spec


def validate_continuous(spec,now):
    families={**V2_FAMILIES,'WARRIOR_MOMENTUM':(*V2_FAMILIES['WARRIOR_MOMENTUM'],'GROK_DIRECTED')}
    library=spec.get('warrior_library_policy')
    if library is not None:
        if library != WARRIOR_LIBRARY_POLICY:raise ValueError('UNKNOWN_WARRIOR_LIBRARY_POLICY')
        families={**families,'WARRIOR_MOMENTUM':warrior_library_families()}
    start=datetime.fromisoformat(spec['created_at'])
    if (spec.get('profile')!=ACCOUNT_SIZING_PROFILE or spec.get('operating_mode')!='CONTINUOUS_PAPER'
            or spec.get('expires_at') is not None or spec.get('limits')!={k:None for k in LIMITS}
            or spec.get('cent_policy')!=V2_CENT_POLICY or spec.get('grok_policy')!=CONTINUOUS_POLICY
            or spec.get('families')!={k:list(v) for k,v in families.items()}
            or not re.fullmatch('[0-9a-f]{32}',spec.get('trial_id',''))
            or start.utcoffset() is None or now<start
            or ('entry_sessions' in spec and spec['entry_sessions']!=sorted(EXTENDED_ENTRY_SESSIONS))
            or ('scalper_clock_policy' in spec and spec['scalper_clock_policy']!=CLOCK_POLICY)):
        raise ValueError('CONTINUOUS_PAPER_SPEC_INVALID')


def configured(configuration,runtime_mode):
    value=os.environ.get(ENV,'').strip()
    if not value:return None
    from app.asset_modules.grok_execution_trial import configured as grok_execution
    grok_lease=grok_execution(configuration)
    if (runtime_mode!='PAPER' or configuration.environment.value!='PAPER' or configuration.live_trading_enabled
        or (configuration.grok_paper_entries_enabled and grok_lease is None) or not configuration.daily_research_enabled):
        raise ValueError('EXPERIMENT_REQUIRES_PAPER_LIVE_OFF_GROK_ORDERS_OFF_AND_CAPTURE')
    path,spec=load(value,allow_expired=True)
    result=PaperExperiment(path,spec);result.grok_execution=grok_lease
    return result


class PaperExperiment(EquityQualificationPolicy):
    def __init__(self,path,spec,clock=lambda:datetime.now(UTC),timer=monotonic):
        super().__init__();self.path=Path(path);self.spec=spec;self.clock=clock;self.timer=timer
        self._started=timer();self._wall_start=clock();self._bindings={};self._lock=RLock();self.capture_ready=None
        self.grok_execution=None
        self.db_path=self.path.with_name('entry-attempts.sqlite3')
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (trial TEXT,request TEXT,notional TEXT,record TEXT,PRIMARY KEY(trial,request))');db.commit()

    @property
    def continuous(self):return self.spec.get('scope')==CONTINUOUS_SCOPE

    @property
    def cent_early(self):return self.spec['scope'] in {CONTINUOUS_SCOPE,'NATIVE_PAPER_EXPERIMENT_V2','NATIVE_PAPER_EXPERIMENT_V3','NATIVE_PAPER_EXPERIMENT_V4','NATIVE_PAPER_EXPERIMENT_V5','NATIVE_PAPER_EXPERIMENT_V6'}

    @property
    def account_sizing(self):return self.spec.get('profile')==ACCOUNT_SIZING_PROFILE

    @property
    def warrior_library_enabled(self):
        return self.continuous and self.spec.get('warrior_library_policy')==WARRIOR_LIBRARY_POLICY

    def warrior_config(self,config):
        if self.warrior_library_enabled:
            return replace(config,taxonomy_paper_execution_enabled=True,
                entry=replace(config.entry,allowed_sessions=EXTENDED_ENTRY_SESSIONS)
                    if self.spec.get('entry_sessions') else config.entry,
                setups=replace(config.setups,allowed_setup_types=None))
        if not self.cent_early:return config
        return replace(config,entry=replace(config.entry,allowed_sessions=EXTENDED_ENTRY_SESSIONS)
            if self.spec.get('entry_sessions') else config.entry,setups=replace(config.setups,
            allowed_setup_types=frozenset(V2_FAMILIES['WARRIOR_MOMENTUM'])))

    def scalper_config(self,config):
        if not self.cent_early:return config
        return replace(config,cent_target_minimum=D('0.05'),cent_target_maximum=D('0.10'),
            provider_future_tolerance_seconds=D('.25') if self.spec.get('scalper_clock_policy')==CLOCK_POLICY else D('0'),
            allowed_sessions=EXTENDED_ENTRY_SESSIONS if self.spec.get('entry_sessions') else config.allowed_sessions,
            minimum_net_target_reward_r=D('1'),cent_pullback_support_enabled=True,
            cent_fill_anchored_economics=self.spec.get('profile') in {'CENT_EXECUTABLE_V3','CENT_DEFENSE_V4',*GROK_CONTROL_PROFILES},
            cent_profit_defense_floor=D('0.05') if self.spec.get('profile') in {'CENT_DEFENSE_V4',*GROK_CONTROL_PROFILES} else None)

    def active(self):
        try:
            # Removal/revocation/expiry all stop new entries; never affect SELLs.
            path,spec=load(self.path,self.clock)
            elapsed=self.timer()-self._started
            wall=(self.clock()-self._wall_start).total_seconds()
            return bool(self.capture_ready and self.capture_ready()
                and (self.grok_execution is None or self.grok_execution.active())
                and not self.path.with_name('PAUSE_ENTRIES').exists()
                and not (self.continuous and self.path.with_name('ACCOUNT_DATA_UNAVAILABLE').exists()) and spec==self.spec and (self.continuous or (0<=elapsed<self.spec['limits']['hours']*3600 and abs(wall-elapsed)<=2)))
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
            and metadata.get('strategy_family') in self.spec['families'].get(owner,())
            and binding==(metadata.get('strategy_code_fingerprint'),metadata.get('strategy_configuration_fingerprint')))

    def claim_order(self,placement):
        order=placement.order;metadata=dict(order.metadata)
        if not self.permits(metadata) or metadata.get('source')!='autonomous-paper' or metadata.get('reason')!='ENTRY':return False
        try:
            qty=order.quantity;price=order.limit_price;stop=D(str(metadata.get('structural_stop','')))
            risk=D(str(metadata.get('risk_dollars','')))
            if (order.order_type.value!='LIMIT' or price is None or any(not v.is_finite() for v in (qty,price,stop,risk))
                or qty<=0 or qty!=qty.to_integral_value() or not 0<stop<price or risk<=0
                or (self.account_sizing and qty*(price-stop)>risk)
                or (not self.account_sizing and (qty>10 or qty*price>100 or risk>1 or qty*(price-stop)>1))):return False
            notional=qty*price
            record=dict(trial_id=self.spec['trial_id'],request_id=order.request_id,symbol=order.symbol,
                strategy_owner=metadata['strategy_owner'],strategy_family=metadata['strategy_family'],
                quantity=str(qty),limit_price=str(price),structural_stop=str(stop),initial_risk=str(risk),
                claimed_at=self.clock().isoformat(),state='RESERVED_BEFORE_GATEWAY_RESULT')
            with self._lock, closing(sqlite3.connect(self.db_path,timeout=2)) as db:
                db.execute('BEGIN IMMEDIATE')
                duplicate=db.execute('SELECT 1 FROM attempts WHERE trial=? AND request=?',(self.spec['trial_id'],order.request_id)).fetchone()
                rows=[] if self.account_sizing else db.execute('SELECT request,notional FROM attempts WHERE trial=?',(self.spec['trial_id'],)).fetchall()
                if duplicate or (not self.account_sizing and
                        (len(rows)>=50 or sum((D(row[1]) for row in rows),D(0))+notional>5000)):
                    db.rollback();return False
                if not self.active():db.rollback();return False
                db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(self.spec['trial_id'],order.request_id,str(notional),json.dumps(record)))
                db.commit()
            return True
        except Exception:return False

    def constrain_placement(self,placement):
        metadata=dict(placement.order.metadata)
        deadline=self.clock()+timedelta(seconds=60)
        if not self.continuous:deadline=min(datetime.fromisoformat(self.spec['expires_at']),deadline)
        if self.grok_execution is not None:
            deadline=min(deadline,datetime.fromisoformat(self.grok_execution.doc['expires_at']))
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
        if self.continuous:
            return ('CONTINUOUS PAPER: '+('entries enabled' if self.active() else 'entries paused or capture unavailable')+
                '; no trial expiry or paid-call quota. Fixed native account and strategy settings. '
                'Grok Warrior fair watchlist review; Scalper native. Position protection remains available.')
        state='entry trial enabled, subject to native checks' if self.active() else ('ENTRY WINDOW EXPIRED; protection only; prepare a new trial for new buys' if self.clock()>=datetime.fromisoformat(self.spec['expires_at']) else 'new entries paused or capture unavailable')
        profile=('Grok-directed Warrior; native five-cent-defense Scalper. ' if self.spec.get('profile') in GROK_CONTROL_PROFILES else 'V4 five-cent stop-defense floor; early native Warrior. ' if self.spec.get('profile')=='CENT_DEFENSE_V4' else 'V3 fill-anchored cent Scalper; early native Warrior. ' if self.spec.get('profile')=='CENT_EXECUTABLE_V3' else 'Early native Warrior; cent-pullback Scalper. ') if self.cent_early else ''
        mode='Grok WARRIOR review + directed entries/exits ON; Scalper native' if self.spec.get('profile') in GROK_CONTROL_PROFILES else 'Grok native order review ON; crypto requires its visible AI switch' if self.grok_execution else 'Grok orders OFF'
        sizing=('Native account sizing; no trial quantity, position, risk, attempt or cumulative-notional overrides; '
            if self.account_sizing else 'Max 10 shares / $100 position / $1 modeled initial risk; 50 shared entry attempts; ')
        return ('EXPERIMENTAL PAPER: '+profile+state+'. '+sizing+'expires '+self.spec['expires_at']+'. '+mode+
                ('. Entry sessions: PREMARKET / REGULAR / AFTER_HOURS' if self.spec.get('entry_sessions') else '')+
                '. Position protection remains available.')

    def status(self):
        return dict(policy=self.spec['scope'],entry_mode='CONTINUOUS_PAPER' if self.continuous else 'BOUNDED_PAPER_EXPERIMENT',approved_policies=[],
            trial_id=self.spec['trial_id'],profile=self.spec.get('profile','LEGACY_V1'),expires_at=self.spec['expires_at'],limits=self.spec['limits'],families=self.spec['families'],
            protective_sells='AVAILABLE',reason='PAPER_OPERATION_NOT_LIVE_QUALIFICATION' if self.continuous else 'EXPERIMENTAL_NOT_RELEASE_QUALIFIED')


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--create',required=True)
    p.add_argument('--profile',choices=PROFILES,default='LEGACY_V1')
    p.add_argument('--extended-sessions',action='store_true')
    p.add_argument('--clock-tolerance',action='store_true')
    p.add_argument('--hours',type=int,choices=(8,24),default=8)
    p.add_argument('--continuous',action='store_true')
    args=p.parse_args();root=Path(args.create)
    spec=load(root/'experiment.json',allow_expired=True)[1] if (root/'experiment.json').exists() else create(root,profile=args.profile,extended_sessions=args.extended_sessions,clock_tolerance=args.clock_tolerance,hours=args.hours,continuous=args.continuous)
    if (spec.get('scope')==CONTINUOUS_SCOPE)!=args.continuous:raise ValueError('EXISTING_OPERATING_MODE_MISMATCH')
    if not args.continuous and spec['limits']['hours']!=args.hours:
        raise ValueError('EXISTING_EXPERIMENT_DURATION_MISMATCH')
    if spec.get('profile','LEGACY_V1') != args.profile:
        raise ValueError('EXISTING_EXPERIMENT_PROFILE_MISMATCH')
    if bool(spec.get('entry_sessions')) != args.extended_sessions:
        raise ValueError('EXISTING_EXPERIMENT_SESSION_POLICY_MISMATCH')
    if bool(spec.get('scalper_clock_policy')) != args.clock_tolerance:
        raise ValueError('EXISTING_EXPERIMENT_CLOCK_POLICY_MISMATCH')
    if spec['expires_at'] is not None and datetime.fromisoformat(spec['expires_at'])<=datetime.now(UTC):
        print('PAPER_EXPERIMENT_EXPIRED; NEW_ENTRIES_DISABLED; OWNED_POSITION_PROTECTION_ONLY')
    print('PAPER_EXPERIMENT_ID='+spec['trial_id']);print('EXPERIMENT_EXPIRES_AT='+str(spec['expires_at']))


if __name__=='__main__':main()
