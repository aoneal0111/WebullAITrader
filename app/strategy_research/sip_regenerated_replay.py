"""Causal SIP detector replay on the previously selected retrospective symbol-days."""
import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from app.live_scanner.session import scanner_session
from app.opportunity_discovery import CompletedBar, DetectionState, DiscoveryContext, FeatureCapabilities, default_registry
from .episode_price_study import BarCache, NY, FAMILIES, POLICIES, COSTS, DayWallet, aggregate
from .episode_gap_audit import bounded_json

VERSION = 'SIP_REGENERATED_FIVE_FAMILY_REPLAY_V1'
FIRED = {DetectionState.DETECTED, DetectionState.TRIGGER_ARMED, DetectionState.STRENGTHENING}


def generate(bars, *, registry=None):
    """No outcomes, IEX labels, future bars, or prior-close estimates enter contexts."""
    detectors = tuple(d for d in (registry or default_registry()).detectors if d.definition.strategy_id in FAMILIES)
    if {d.definition.strategy_id for d in detectors} != set(FAMILIES):
        raise ValueError('FIVE_DETECTORS_REQUIRED')
    events, seen, counts = [], set(), Counter()
    for index, current in enumerate(bars):
        window = bars[max(0, index + 1 - 64):index + 1]
        completed = tuple(CompletedBar(b.symbol, b.timestamp + timedelta(minutes=1), b.open, b.high,
            b.low, b.close, b.volume, scanner_session(b.timestamp).value) for b in window)
        cutoff = current.timestamp + timedelta(minutes=1)
        if any(b.completed_at > cutoff for b in completed):
            raise ValueError('FUTURE_DETECTOR_BAR')
        regular = tuple(b for b in completed if b.session.upper() == 'REGULAR')
        premarket = tuple(b for b in completed if b.session.upper() == 'PREMARKET')
        context = DiscoveryContext(current.symbol, current.timestamp.astimezone(NY).date(),
            scanner_session(current.timestamp).value, cutoff, completed,
            FeatureCapabilities(completed_bars=bool(completed), impulse_history=len(completed)>=3,
                pullback_history=len(completed)>=3, session_hod=bool(completed),
                opening_range=len(regular)>=5, premarket_history=bool(premarket), prior_close=False))
        prefix_hash = sha256(json.dumps([(b.timestamp.isoformat(),str(b.open),str(b.high),str(b.low),str(b.close),str(b.volume))
            for b in window],separators=(',',':')).encode()).hexdigest()
        for detector in detectors:
            detection = detector.detect(context)
            counts[detection.strategy_id + ':' + detection.state.value] += 1
            if detection.state not in FIRED:
                continue
            trigger, stop = detection.trigger_level, detection.structural_stop
            if trigger is None or stop is None or not (trigger.is_finite() and stop.is_finite() and trigger > stop > 0):
                counts['INVALID_GEOMETRY'] += 1
                continue
            key = (detection.strategy_id, detection.opportunity_anchor)
            if key in seen:
                counts['REPEATED_FAMILY_ANCHOR'] += 1
                continue
            seen.add(key)
            ident = sha256(f'{VERSION}|{current.symbol}|{cutoff.isoformat()}|{key}'.encode()).hexdigest()
            events.append(dict(episode_id=ident, symbol=current.symbol,
                day=current.timestamp.astimezone(NY).date().isoformat(), detected_at=cutoff.isoformat(),
                planned_price=str(trigger), stop=str(stop), families=[detection.strategy_id], prefix_ok=True,
                context_sha256=prefix_hash, detector_version=detection.strategy_version,
                detector_state=detection.state.value, anchor=detection.opportunity_anchor,
                entry_at=(cutoff.replace(second=0,microsecond=0)+timedelta(minutes=1)).astimezone(UTC).isoformat()))
    return events, dict(counts)


def frozen_plan():
    return dict(scope=VERSION, families=FAMILIES, exits=[p.name for p in POLICIES], costs=[str(c) for c in COSTS],
        detector_context='Existing five registry detectors; last 64 observed completed bars, sessions from source open time',
        firing_states=sorted(s.value for s in FIRED), dedupe='First fired state per family/anchor within symbol-day',
        prior_close='Unavailable; never estimated from this day',
        entry='Exact minute after detection minute; same next-open proxy and 1% preceding-volume sizing as prior study',
        wallet='Independent $1000 day per family/exit/cost; max two positions, $500 per entry, $10 risk, 60-minute hold',
        unresolved='Missing bars and intrabar ambiguity remain unresolved; reservations retained, days excluded from headline',
        selection='Same outcome-inspected 600 symbol-days as original IEX study, not a fresh holdout',
        empty_partitions='Retain in manifest; exclude their entire day from headline for every case',
        comparison='Same resolved dates across eight exit/cost cases within each family; no cross-family ranking on different dates')


def run(history, output, *, progress=print):
    history, output = Path(history).resolve(), Path(output)
    manifest_path = history/'manifest.json'
    manifest = bounded_json(manifest_path, 4_000_000)
    if manifest.get('scope')!='SELECTED_HISTORICAL_SIP_CAPTURE_V1' or manifest.get('feed')!='sip' or manifest.get('adjustment')!='raw' or not manifest.get('status','').startswith('COMPLETE'):
        raise ValueError('COMPLETED_RAW_SIP_CAPTURE_REQUIRED')
    partitions = manifest['partitions']
    if not partitions or len(partitions)>600 or len(partitions)!=manifest['selected_partitions']:
        raise ValueError('PARTITION_COUNT_BOUND')
    keys=set();grouped=defaultdict(list)
    for p in partitions:
        symbol, day = p['symbol'], p['day']
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}',symbol) or not '2026-06-01'<=day<='2026-08-31' or (symbol,day) in keys:
            raise ValueError('PARTITION_IDENTITY_OR_DUPLICATE')
        datetime.strptime(day,'%Y-%m-%d');keys.add((symbol,day));grouped[day].append(p)
    output.mkdir(parents=True,exist_ok=False)
    plan=frozen_plan()
    source_paths=[Path(__file__), Path(__file__).with_name('episode_price_study.py'), Path(__file__).with_name('episode_exit_mechanics.py')]
    import app.opportunity_discovery.detectors as detector_module
    import app.opportunity_discovery.context as context_module
    source_paths += [Path(detector_module.__file__),Path(context_module.__file__)]
    plan['source_hashes']={p.name:sha256(p.read_bytes()).hexdigest() for p in source_paths}
    plan['capture_manifest_sha256']=sha256(manifest_path.read_bytes()).hexdigest()
    plan_path=output/'frozen-plan.json';plan_path.write_text(json.dumps(plan,indent=2),encoding='utf-8')
    cache=BarCache(history/'normalized');case_days={};counts=Counter();empty=[]
    try:
        with (output/'private-signals.jsonl').open('x',encoding='utf-8') as signals, (output/'private-trades.jsonl').open('x',encoding='utf-8') as trades:
            def trace(record):trades.write(json.dumps(record,default=str,separators=(',',':'))+'\n')
            registry=default_registry()
            for day, parts in sorted(grouped.items()):
                events=[];unavailable=0
                for p in sorted(parts,key=lambda p:p['symbol']):
                    symbol=p['symbol'];bars=cache.get(symbol,day)
                    if cache.proofs[(symbol,day)][2]!=p['sha256'] or len(bars)!=p['rows']:
                        raise ValueError('CAPTURE_PRICE_HASH_OR_ROW_MISMATCH')
                    if not bars:
                        empty.append(dict(symbol=symbol,day=day));unavailable+=1;continue
                    generated, detail=generate(bars,registry=registry);events.extend(generated);counts.update(detail)
                    counts['nonempty_partitions']+=1;counts['observed_bars']+=len(bars)
                    if len(events)>50000:raise ValueError('DAILY_SIGNAL_BOUND')
                lanes={(f,p.name,str(c)):DayWallet(f,p,c,trace) for f in FAMILIES for p in POLICIES for c in COSTS}
                if unavailable:
                    for lane in lanes.values():lane.counts['INVALID_OR_MISSING_PRICE_PARTITION']+=unavailable
                for e in sorted(events,key=lambda e:(e['entry_at'],e['symbol'],e['episode_id'])):
                    signals.write(json.dumps(e,separators=(',',':'))+'\n');counts['unique_family_signals']+=1
                    at=datetime.fromisoformat(e['entry_at']);bars=cache.get(e['symbol'],day)
                    for (f,_,_),lane in lanes.items():
                        if f in e['families']:lane.consider(e,bars,at)
                for key,lane in lanes.items():case_days.setdefault(key,[]).append(lane.finish(day))
                progress(f'SIP_REPLAY_DATE={day}; FAMILY_SIGNALS={len(events)}',flush=True)
        cache.verify_stable()
        if sha256(manifest_path.read_bytes()).hexdigest()!=plan['capture_manifest_sha256']:
            raise ValueError('CAPTURE_MANIFEST_CHANGED')
        common={f:set.intersection(*({d['day'] for d in days if d['fully_resolved'] and d['input_complete'] and d['signal_evidence_complete']}
            for (family,_,_),days in case_days.items() if family==f)) for f in FAMILIES}
        cases=[]
        for (f,p,c),days in case_days.items():
            cases.append(dict(family=f,policy=p,cost_per_share_per_side=c,**aggregate(days),
                paired_common_day_metrics=aggregate([d for d in days if d['day'] in common[f]]),
                months={m:aggregate([d for d in days if d['day'].startswith(m)]) for m in ('2026-06','2026-07','2026-08')}))
            (output/f'private-days-{f}-{p}-{c}.json').write_text(json.dumps(days,default=str),encoding='utf-8')
        result=dict(scope=VERSION,evidence_status='REGENERATED_SIP_PRICE_REPLAY_WITH_ENTRIES' if any(c['counts'].get('entries') for c in cases) else 'NO_ENTRIES',
            plan_sha256=sha256(plan_path.read_bytes()).hexdigest(),capture_manifest_sha256=plan['capture_manifest_sha256'],
            detector_counts=dict(counts),empty_partitions=empty,common_resolved_dates={f:sorted(v) for f,v in common.items()},cases=cases,
            orders=0,network_requests=0,grok_requests=0,promotion='NONE',execution_authorized=False,
            limitations=['Retrospective sample selected from previously inspected IEX episodes; not a new holdout or whole-market test.',
                'SIP signals regenerated from completed-bar prefixes; no old episode outcome labels used.',
                'Existing detector firing states include armed setups; this is a detector research protocol, not production qualification.',
                'Family/anchor deduplication is independent per family; no claim of identical multi-family miner grouping.',
                'Session HOD and opening references use the existing 64-observed-bar context, not a new full-session detector.',
                'Missing rows may reflect no eligible trades; no bars filled or gaps skipped.',
                'Next-minute OHLC proxies do not validate executable bid/ask, halts, queue position, latency, or account costs.',
                'Daily wallets reset; partial proceeds unavailable until full exit; no continuous account return.',
                'No strategy, trading permission, Grok order path or protective exit modified.'])
        path=output/'sip-regenerated-replay-summary.json';path.write_text(json.dumps(result,default=str,indent=2),encoding='utf-8')
        progress('UPLOAD_SUMMARY='+str(path),flush=True)
        return result
    except Exception as exc:
        reason=str(exc) if type(exc) is ValueError else 'INPUT_OR_REPLAY_FAILURE'
        (output/'failure.json').write_text(json.dumps(dict(status='FAILED',reason=reason,orders=0)),encoding='utf-8')
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.history,args.output)


if __name__=='__main__':main()
