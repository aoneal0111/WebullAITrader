"""Fixed five-minute crypto quote capture; no runtime, strategy or order access."""
from collections import Counter
from datetime import UTC,datetime
from time import monotonic,sleep

from .models import CryptoPair
from .provider import (CryptoRateLimitError,UnsupportedCryptoSymbolError,
    MalformedCryptoQuoteError,CryptoBusinessRequestError,CryptoProviderError)
from .readiness import evaluate_snapshot

SLOTS=60
INTERVAL=5
DEADLINE=300


def category(exc):
    return next((name for cls,name in (
        (PermissionError,'PERMISSION_DENIED'),(CryptoRateLimitError,'RATE_LIMITED'),
        (UnsupportedCryptoSymbolError,'UNSUPPORTED_PAIR'),(MalformedCryptoQuoteError,'MALFORMED_QUOTE'),
        (CryptoBusinessRequestError,'BUSINESS_REQUEST_ERROR'),(CryptoProviderError,'PROVIDER_ERROR'))
        if isinstance(exc,cls)),'READ_FAILED')


def capture(provider,*,clock=lambda:datetime.now(UTC),timer=monotonic,sleeper=sleep,progress=print):
    """Poll once per slot, never catch up in bursts; a 429 defers reads for60s.

    Request timeouts belong to the bounded data-only provider. The scheduling
    deadline can be exceeded by one in-flight request. No errors are injected
    into real market data. Recovery is observed only after an actual bad sample.
    """
    report=dict(scope='READ_ONLY_CRYPTO_QUOTE_RELIABILITY_V1',orders=0,grok_requests=0,
        strategy_changes=0,ready=False,requested_slots=SLOTS,interval_seconds=INTERVAL,
        deadline_seconds=DEADLINE,snapshot_attempts=0,samples=[],recoveries_observed=0,
        clock_discontinuities=0)
    started=clock();begin=timer();last_wall=started;last_mono=begin;cooldown=begin
    report['started_at']=started.isoformat();unhealthy=False;terminal=None
    try:
        pairs=provider.discover((CryptoPair.configured('BTC/USD'),CryptoPair.configured('ETH/USD')))
        if len(pairs)!=2 or {p.canonical_symbol for p in pairs}!={'BTC/USD','ETH/USD'}:
            raise UnsupportedCryptoSymbolError('discovery coverage mismatch')
        due=begin
        for slot in range(SLOTS):
            current=timer()
            if current>=begin+DEADLINE:terminal='DEADLINE_REACHED';break
            if current<due:sleeper(due-current)
            current=timer()
            if current>=begin+DEADLINE:terminal='DEADLINE_REACHED';break
            sample=dict(at=clock().isoformat(),slot=slot+1,complete=False,symbols=[])
            if current<cooldown:
                sample['status']='RATE_LIMIT_BACKOFF';unhealthy=True
            else:
                report['snapshot_attempts']+=1;before=timer()
                sample['request_start_elapsed_seconds']=round(before-begin,6)
                try:
                    rows=provider.snapshots(pairs)
                    sample.update(evaluate_snapshot(pairs,rows,clock()))
                    sample['status']='FRESH_COMPLETE' if sample['complete'] else 'COVERAGE_OR_FRESHNESS_FAILED'
                    if sample['complete'] and unhealthy:
                        report['recoveries_observed']+=1;unhealthy=False
                    elif not sample['complete']:unhealthy=True
                except Exception as exc:
                    sample['status']=category(exc);unhealthy=True
                    if isinstance(exc,CryptoRateLimitError):cooldown=timer()+60
                    if isinstance(exc,(PermissionError,UnsupportedCryptoSymbolError,CryptoBusinessRequestError)):
                        terminal=sample['status']
                sample['request_seconds']=round(timer()-before,6)
            now=clock();after=timer()
            delta=(now-last_wall).total_seconds()-(after-last_mono)
            if abs(delta)>.25 or after<last_mono:
                report['clock_discontinuities']+=1
            last_wall,last_mono=now,after
            report['samples'].append(sample)
            if (slot+1)%10==0 or terminal:
                count=sum(s['complete'] for s in report['samples'])
                progress(f'CRYPTO_RELIABILITY_SAMPLES={slot+1}/{SLOTS}; FRESH_COMPLETE={count}',flush=True)
            if terminal:break
            # Schedule relative to this attempt's start as well as the original
            # cadence. A slow response cannot create an immediate retry burst.
            due=max(begin+(slot+1)*INTERVAL,current+INTERVAL)
    except KeyboardInterrupt:terminal='INTERRUPTED'
    except Exception as exc:terminal=category(exc)
    report['finished_at']=clock().isoformat();report['elapsed_seconds']=round(timer()-begin,6)
    samples=report['samples'];report['sampled_slots']=len(samples)
    report['fresh_complete_samples']=sum(s['complete'] for s in samples)
    report['status_counts']=dict(Counter(s['status'] for s in samples))
    durations=[s['request_seconds'] for s in samples if 'request_seconds' in s]
    starts=[s['request_start_elapsed_seconds'] for s in samples if 'request_start_elapsed_seconds' in s]
    report['maximum_request_seconds']=max(durations) if durations else None
    report['maximum_attempt_start_gap_seconds']=max((b-a for a,b in zip(starts,starts[1:])),default=None)
    report['recovery_verification']=('OBSERVED_BAD_TO_FRESH_TRANSITION' if report['recoveries_observed']
        else 'NO_RECOVERY_OBSERVED')
    report['symbols']={}
    for symbol in ('BTC/USD','ETH/USD'):
        rows=[r for s in samples for r in s['symbols'] if r['symbol']==symbol]
        ages=[r['quote_age_seconds'] for r in rows if 'quote_age_seconds' in r]
        report['symbols'][symbol]=dict(expected_samples=SLOTS,evaluated_symbol_samples=len(rows),
            quotes_present_samples=len(ages),unobserved_slots=SLOTS-len(rows),
            usable_bid_ask_samples=sum(r.get('bid_ask_present',False) for r in rows),
            entry_fresh_samples=sum(r.get('entry_fresh_2s',False) for r in rows),
            missing_or_duplicate_samples=sum(r.get('status')=='MISSING_OR_DUPLICATE' for r in rows),
            future_samples=sum(a<0 for a in ages),stale_samples=sum(a>2 for a in ages),
            minimum_age_seconds=min(ages) if ages else None,maximum_age_seconds=max(ages) if ages else None)
    report['ready']=(terminal is None and len(samples)==SLOTS and all(s['complete'] for s in samples)
        and report['clock_discontinuities']==0 and report['elapsed_seconds']>=(SLOTS-1)*INTERVAL)
    report['status']=terminal or ('BOUNDED_WINDOW_FRESH' if report['ready'] else 'WINDOW_NOT_FULLY_FRESH')
    report['limitations']=[
        'Two pairs over about five minutes; no continuous stream, historical-bar or strategy validation',
        'Freshness measured after each response; gaps between polls are not continuously observed',
        'No provider failures are deliberately induced; zero recoveries does not verify recovery behavior',
        'Clock checks compare wall-time progress with monotonic progress, not an independent NTP authority',
        'Positive samples do not grant crypto activation or Grok order authority']
    return report
