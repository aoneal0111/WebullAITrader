"""Read-only provider bars -> native crypto adapter -> unchanged signal rules.

By default no runtime, worker controller, supervisor, ledger, model or order service starts.
Any hypothetical proposal returned by the pure signal function is discarded.
The provider-worker diagnostic can inject a disabled temporary handoff explicitly.
"""
from collections import Counter
from dataclasses import asdict
from datetime import UTC,datetime
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic,sleep

from app.asset_modules.crypto_history_adapter import adapt
from app.asset_modules.crypto_signals import POLICY,CryptoQuote,CryptoSignalSettings,propose
from .evidence import CryptoBarInterval,normalize_crypto_rows
from .models import CryptoPair
from .reliability import category


def check(provider,*,clock=lambda:datetime.now(UTC),timer=monotonic,sleeper=sleep,progress=print,handoff=None):
    settings=CryptoSignalSettings();baseline=asdict(settings)
    report=dict(scope='READ_ONLY_CRYPTO_BAR_QUOTE_ALIGNMENT_V1',policy=POLICY,
        settings_sha256=sha256(json.dumps(baseline,default=str,sort_keys=True).encode()).hexdigest(),
        orders=0,grok_requests=0,strategy_changes=0,proposals_submitted=0,
        requested_cycles=3,cycle_interval_seconds=65,deadline_seconds=180,
        history_attempts=0,snapshot_attempts=0,cycles=[],ready=False)
    root=Path(__file__).resolve().parents[1]
    names=('asset_modules/crypto_signals.py','asset_modules/crypto_history_adapter.py',
        'asset_modules/contract_logic.py','crypto_research/evidence.py','crypto_research/provider.py',
        'crypto_research/bar_alignment.py')
    report['source_hashes']={name:sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in names}
    started=timer();last_wall=clock();last_mono=started;seen={};previous_ends={};terminal=None;clock_steps=0
    report['started_at']=last_wall.isoformat()
    try:
        pairs=provider.discover((CryptoPair.configured('BTC/USD'),CryptoPair.configured('ETH/USD')))
        if len(pairs)!=2 or {p.canonical_symbol for p in pairs}!={'BTC/USD','ETH/USD'}:
            raise ValueError('pair coverage mismatch')
        for cycle_number in range(3):
            due=started+cycle_number*65
            wait=due-timer()
            while wait>0:
                sleeper(min(wait,5));wait=due-timer()
            if timer()-started>=180:terminal='DEADLINE_REACHED';break
            cycle=dict(cycle=cycle_number+1,history=[],symbols=[],complete=False)
            report['cycles'].append(cycle);history={};normalizations={};revisions={}
            # Availability is fixed at each actual response receipt. Future bars
            # are excluded before quotes are requested, never retimed afterwards.
            for pair in pairs:
                report['history_attempts']+=1
                rows=provider.historical_bars(pair,timespan='M1',count=64,real_time_required=False)
                if len(rows)>64:raise ValueError('history response exceeds requested bound')
                received=clock()
                normalized=normalize_crypto_rows(pair,rows,interval=CryptoBarInterval.M1,
                    observed_at=received,decision_cutoff=received)
                history[pair.canonical_symbol]=normalized.bars
                normalizations[pair.canonical_symbol]=normalized
                revision_count=0
                for b in normalized.bars:
                    identity=(pair.canonical_symbol,b.opened_at)
                    values=(b.open,b.high,b.low,b.close,b.volume)
                    if identity in seen and seen[identity]!=values:revision_count+=1
                    seen[identity]=values
                revisions[pair.canonical_symbol]=revision_count
                cycle['history'].append(dict(symbol=pair.canonical_symbol,received_at=received.isoformat(),
                    rows=len(rows),completed_bars=len(normalized.bars),malformed_rows=normalized.malformed_rows,
                    incomplete_rows=normalized.incomplete_rows,duplicate_rows=normalized.duplicate_rows,
                    revisions=revision_count,
                    provider_fields=sorted({str(k)[:64] for row in rows[:2] for k in row})[:64]))
            report['snapshot_attempts']+=1
            quotes=provider.snapshots(pairs);now=clock();by_symbol={}
            for q in quotes:by_symbol.setdefault(q.pair.canonical_symbol,[]).append(q)
            cycle['decision_cutoff']=now.isoformat()
            for pair in pairs:
                symbol=pair.canonical_symbol;items=by_symbol.get(symbol,[])
                bars=history[symbol];n=normalizations[symbol];quote=items[0] if len(items)==1 else None
                frame=adapt(symbol,bars,quote,now=now)
                off_grid=sum(b.opened_at.second!=0 or b.opened_at.microsecond!=0 for b in bars)
                clean=(frame.status=='READY' and n.incomplete_rows<=1
                       and not(n.malformed_rows or n.duplicate_rows or off_grid or revisions[symbol]))
                reason='NOT_ASSESSED';hypothetical=False
                handoff_result=None
                if clean:
                    q=CryptoQuote(symbol,quote.bid,quote.ask,quote.timestamp)
                    proposal,reason=propose(frame.history,q,now,settings)
                    hypothetical=proposal is not None # Deliberately discard every proposal.
                    if handoff is not None:
                        handoff_result=handoff(frame.history,quote,settings)
                        clean &= handoff_result['ready'] is True
                previous_end=previous_ends.get(symbol)
                advanced=None if cycle_number==0 else bool(bars and previous_end and bars[-1].closed_at>previous_end)
                previous_ends[symbol]=bars[-1].closed_at if bars else None
                cycle['symbols'].append(dict(symbol=symbol,adapter_status=frame.status,
                    input_ready=clean,worker_bar_count=frame.worker_bar_count,
                    off_grid_bars=off_grid,quote_count=len(items),
                    quote_age_seconds=None if quote is None else (now-quote.timestamp).total_seconds(),
                    latest_bar_close=None if not bars else bars[-1].closed_at.isoformat(),
                    latest_bar_age_seconds=None if not bars else (now-bars[-1].closed_at).total_seconds(),
                    latest_bar_advanced=advanced,
                    signal_assessment=reason,hypothetical_proposal=hypothetical))
                if handoff_result is not None:
                    cycle['symbols'][-1]['handoff']=handoff_result
            wall=clock();mono=timer()
            if abs((wall-last_wall).total_seconds()-(mono-last_mono))>.25:clock_steps+=1
            last_wall,last_mono=wall,mono
            cycle['complete']=(all(r['input_ready'] and r['latest_bar_advanced'] is not False for r in cycle['symbols'])
                and set(by_symbol)=={p.canonical_symbol for p in pairs})
            progress(f"CRYPTO_BAR_ALIGNMENT_CYCLE={cycle_number+1}/3; INPUTS_READY={cycle['complete']}",flush=True)
    except KeyboardInterrupt:terminal='INTERRUPTED'
    except Exception as exc:terminal=category(exc)
    report['finished_at']=clock().isoformat();report['elapsed_seconds']=round(timer()-started,6)
    report['clock_discontinuities']=clock_steps
    report['ready']=(terminal is None and len(report['cycles'])==3
        and all(c['complete'] for c in report['cycles']) and clock_steps==0 and timer()-started>=130)
    report['status']=terminal or ('BOUNDED_BAR_QUOTE_ALIGNMENT_PASSED' if report['ready'] else 'BAR_OR_QUOTE_ALIGNMENT_FAILED')
    report['assessment_counts']=dict(Counter(r['signal_assessment'] for c in report['cycles'] for r in c['symbols']))
    report['limitations']=[
        'Two pairs and three cycles; input readiness does not establish profitability or execution permission',
        'Calls use the existing pure signal function and compiled default settings; no proposals are submitted',
        'Incomplete current candles are excluded, never forward-filled or manufactured from snapshots',
        'No runtime activation, worker/ledger round trip, fill simulation or Grok calls are performed',
        'Historical revisions or minute-boundary publication delays remain visible as failed inputs']
    return report
