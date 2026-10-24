"""Bounded crypto market-data reads only. No runtime, strategy, AI or orders."""
from datetime import UTC,datetime
import json

from .models import CryptoPair
from .provider import (CryptoRateLimitError,UnsupportedCryptoSymbolError,
    MalformedCryptoQuoteError,CryptoBusinessRequestError,CryptoProviderError)


def evaluate_snapshot(pairs,rows,now):
    by_pair={}
    for row in rows:by_pair.setdefault(row.pair.canonical_symbol,[]).append(row)
    sample=dict(at=now.isoformat(),symbols=[],complete=True)
    for pair in pairs:
        items=by_pair.get(pair.canonical_symbol,[])
        if len(items)!=1:
            sample['symbols'].append(dict(symbol=pair.canonical_symbol,status='MISSING_OR_DUPLICATE'))
            sample['complete']=False;continue
        row=items[0];age=(now-row.timestamp).total_seconds()
        bid_ask=row.bid is not None and row.ask is not None and 0<row.bid<=row.ask
        fresh=bid_ask and 0<=age<=2
        sample['symbols'].append(dict(symbol=pair.canonical_symbol,quote_age_seconds=age,
            bid_ask_present=bid_ask,entry_fresh_2s=fresh,research_fresh_120s=0<=age<=120))
        sample['complete'] &= fresh
    if set(by_pair)-{p.canonical_symbol for p in pairs}:sample['complete']=False
    return sample


def check(provider,*,clock=lambda:datetime.now(UTC)):
    requested=(CryptoPair.configured('BTC/USD'),CryptoPair.configured('ETH/USD'))
    report=dict(scope='READ_ONLY_CRYPTO_QUOTE_READINESS',orders=0,grok_requests=0,
        strategy_changes=0,snapshot_attempts=0,samples=[],ready=False)
    try:
        pairs=provider.discover(requested)
        if {p.canonical_symbol for p in pairs}!={'BTC/USD','ETH/USD'}:
            raise UnsupportedCryptoSymbolError('discovery coverage mismatch')
        for _ in range(3):
            report['snapshot_attempts']+=1
            report['samples'].append(evaluate_snapshot(pairs,provider.snapshots(pairs),clock()))
        report['ready']=all(s['complete'] for s in report['samples'])
        report['status']='BOUNDED_SAMPLES_FRESH' if report['ready'] else 'COVERAGE_OR_FRESHNESS_FAILED'
    except Exception as exc:
        report['status']=next((name for cls,name in (
            (PermissionError,'PERMISSION_DENIED'),(CryptoRateLimitError,'RATE_LIMITED'),
            (UnsupportedCryptoSymbolError,'UNSUPPORTED_PAIR'),(MalformedCryptoQuoteError,'MALFORMED_QUOTE'),
            (CryptoBusinessRequestError,'BUSINESS_REQUEST_ERROR'),(CryptoProviderError,'PROVIDER_ERROR'))
            if isinstance(exc,cls)),'READ_FAILED')
    return report


def main():
    import argparse,os
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reliability',action='store_true',help='Run the fixed five-minute read-only capture')
    p.add_argument('--online-read',action='store_true',help='Allow bounded authenticated market-data GETs')
    args=p.parse_args()
    if args.output.exists():p.error('output exists; use a new filename')
    if not args.online_read:p.error('--online-read is required; no requests were made')
    # Resolve exactly the local scoped market-data identity. Never print config,
    # key values, provider messages or response bodies.
    try:
        from dotenv import dotenv_values
        from app.configuration.environment import resolve_runtime_environment
        from app.configuration.loader import load_configuration
        from app.webull.client_factories import MarketDataClientFactory,market_data_configuration
        from .provider import WebullCryptoResearchProvider
        env=resolve_runtime_environment({**dotenv_values(args.repo/'.env'),**os.environ},dotenv_path=args.repo/'.env')
        config=load_configuration(env)
        if config.environment.value!='PAPER' or config.live_trading_enabled or config.grok_paper_entries_enabled:
            raise ValueError('unsafe trading mode')
        data=market_data_configuration(config)
        client=MarketDataClientFactory(data).create(timeout_seconds=5)
        provider=WebullCryptoResearchProvider(client,retry_attempts=0,maximum_symbols=2,maximum_pages=1)
        if args.reliability:
            from .reliability import capture
            report=capture(provider)
        else:report=check(provider)
        report['market_data_environment']=data.environment.value
    except Exception:
        report=dict(scope='READ_ONLY_CRYPTO_QUOTE_RELIABILITY_V1' if args.reliability else 'READ_ONLY_CRYPTO_QUOTE_READINESS',status='LOCAL_CONFIGURATION_OR_CLIENT_FAILED',
                    ready=False,orders=0,grok_requests=0,strategy_changes=0)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print('CRYPTO_READINESS='+report['status']);print('UPLOAD_SUMMARY='+str(args.output.resolve()))
    return 0 if report['ready'] else 2


if __name__=='__main__':raise SystemExit(main())
