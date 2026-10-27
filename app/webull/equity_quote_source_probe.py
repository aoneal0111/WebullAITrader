"""Bounded explicit stock-data comparisons; never execution authorization."""
from datetime import UTC,datetime
import json
from pathlib import Path
from itertools import islice
import math
import re
from time import monotonic,sleep

SYMBOLS=('SPY','AAPL')
TIME_FIELDS=frozenset({'time','timestamp','quote_time','bid_time','ask_time','trade_time',
                      'last_trade_time','quote_timestamp','bid_timestamp','ask_timestamp'})
FIELDS=TIME_FIELDS|{'data','items','symbol','bids','asks','bid','ask','price','size','quotes'}


def describe(value,received_at):
    """Fixed field names and parsed timestamps only; no raw body/price/account."""
    from app.strategies.warrior_momentum.execution_quote import _provider_time
    remaining=[256];times=[];structures=[]
    def visit(node,path='',depth=0):
        remaining[0]-=1
        if remaining[0]<0 or depth>5:return
        if isinstance(node,dict):
            names=sorted(k for k in FIELDS if k in node)
            structures.append(dict(path=path,fields=names))
            for key in names:
                child=node[key];location=(path+'.' if path else '')+key
                if key in TIME_FIELDS:
                    try:
                        if len(str(child))>64:raise ValueError('Timestamp bound exceeded')
                        at=_provider_time({key:child},key)
                        if not 2000<=at.year<=2100:raise ValueError('Time outside diagnostic bounds')
                        times.append(dict(path=location,parsed=True,utc=at.isoformat(),
                            age_ms=round((received_at-at).total_seconds()*1000,3),
                            interpretation='EPOCH_UNIT_HEURISTIC_OR_ISO_NOT_EXECUTION_VALIDATED'))
                    except Exception:times.append(dict(path=location,parsed=False))
                elif isinstance(child,(dict,list)):visit(child,location,depth+1)
        elif isinstance(node,list):
            structures.append(dict(path=path,count=len(node)))
            for child in node[:2]:visit(child,path+'[]',depth+1)
    visit(value)
    return dict(known_structure=structures,timestamp_fields=times,
                inspection_truncated=remaining[0]<0,execution_authorized=False)


def validated_symbols(values):
    symbols=tuple(islice(values,3))
    if (not 1<=len(symbols)<=2 or len(set(symbols))!=len(symbols)
            or any(not isinstance(x,str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}',x) for x in symbols)):
        raise ValueError('One or two unique canonical equity symbols required')
    return symbols


def _instant(value):
    if not isinstance(value,str) or len(value)>64:raise ValueError('Bounded timestamp required')
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.utcoffset() is None:raise ValueError('Aware timestamp required')
    return at.astimezone(UTC)


def select_recent_rejections(directory,*,now):
    """Read bounded recent checkpoint evidence; export no other local data."""
    result=dict(status='NO_RECENT_STALE_QUOTE_REJECTIONS',symbols=[],evidence=[],
                scope='RECENT_WARRIOR_NATIVE_FUNNEL_CHECKPOINT_NOT_RUNTIME_ATTESTATION')
    if now.utcoffset() is None:raise ValueError('Aware comparison time required')
    directory=Path(directory)
    if not directory.is_dir():result['status']='CHECKPOINT_DIRECTORY_MISSING';return result
    candidates=list(islice(directory.glob('*.json'),513))
    if len(candidates)>512:result['status']='CHECKPOINT_SCAN_LIMIT';return result
    files=[]
    for path in candidates:
        try:
            if not path.is_symlink() and path.is_file():files.append((path.stat().st_mtime,path))
        except OSError:continue
    remaining=16*1024*1024
    for _,path in sorted(files,key=lambda x:x[0],reverse=True)[:5]:
        try:
            with path.open('rb') as stream:raw=stream.read(remaining+1)
            if len(raw)>remaining:result['status']='CHECKPOINT_BYTE_LIMIT';return result
            remaining-=len(raw);data=json.loads(raw)
            if not isinstance(data,dict) or data.get('status')!='CHECKPOINT':continue
            captured=_instant(data.get('captured_at'));started=_instant(data.get('runtime_started_at'))
            if not 0<=(now-captured).total_seconds()<=120 or started>captured:continue
            conversion=data.get('entry_conversion')
            if not isinstance(conversion,dict):
                result['status']='CHECKPOINT_FUNNEL_UNAVAILABLE';return result
            records=conversion.get('funnel_records',[])
            if not isinstance(records,list) or len(records)>8192:
                result['status']='CHECKPOINT_FUNNEL_UNAVAILABLE';return result
            found={}
            for row in records:
                if not isinstance(row,dict):continue
                reason=row.get('reason') or row.get('outcome')
                if row.get('stage')!='EXECUTION_QUOTE_REJECTED' or reason!='QUOTE_REJECTED_BID_STALE':continue
                try:
                    at=_instant(row.get('timestamp'));symbol=validated_symbols([row.get('symbol')])[0]
                    age=float(row.get('bid_timestamp_age'))
                    if not started<=at<=captured or not 0<=(now-at).total_seconds()<=300:continue
                    if not math.isfinite(age) or age<=5:continue # No future-only/last-only failures.
                except (ValueError,TypeError):continue
                old=found.get(symbol)
                if old is None or at>old[0]:found[symbol]=(at,age)
            if not found:return result
            selected=sorted(found.items(),key=lambda x:(x[1][0],x[0]),reverse=True)[:2]
            result.update(status='RECENT_STALE_QUOTE_REJECTIONS_SELECTED',
                checkpoint_age_ms=round((now-captured).total_seconds()*1000,3),
                symbols=[x[0] for x in selected],
                evidence=[dict(symbol=symbol,rejection_at=at.isoformat(),
                    evidence_age_ms=round((now-at).total_seconds()*1000,3),recorded_bid_age_ms=round(age*1000,3))
                    for symbol,(at,age) in selected])
            return result # Never combine different runtime checkpoints.
        except (OSError,ValueError,TypeError):continue
    return result


def source_summary(records):
    groups={}
    for row in records:
        key=(row['symbol'],row['operation']);group=groups.setdefault(key,[])
        for item in row.get('inspection',{}).get('timestamp_fields',[]):
            if item.get('parsed') and item['path'].split('.')[-1]=='quote_time':group.append(item)
    return [dict(symbol=symbol,operation=operation,quote_time_samples=len(items),
        quote_age_ms_min=min((x['age_ms'] for x in items),default=None),
        quote_age_ms_max=max((x['age_ms'] for x in items),default=None),
        quote_time_advanced=None if len(items)<2 else _instant(items[-1]['utc'])>_instant(items[0]['utc']))
        for (symbol,operation),items in groups.items()]


def run(market_data,*,symbols=SYMBOLS,rounds=1,clock=lambda:datetime.now(UTC),timer=monotonic,wait=sleep):
    from app.webull.sdk_market_data import _response_value
    from app.webull.options_data_probe import safe_exception_details
    symbols=validated_symbols(symbols)
    if type(rounds) is not int or not 1<=rounds<=2:raise ValueError('One or two bounded rounds required')
    plan=[(round_index+1,symbol,operation) for round_index in range(rounds) for symbol in symbols
          for operation in (('SNAPSHOT','DEPTH') if round_index%2==0 else ('DEPTH','SNAPSHOT'))]
    records=[];attempts=0
    for round_number,symbol,operation in plan:
        response=None;started=timer();attempts+=1
        item=dict(round=round_number,symbol=symbol,operation=operation)
        try:
            response=(market_data.get_snapshot(symbols=[symbol],category='US_STOCK',extend_hour_required=True)
                      if operation=='SNAPSHOT' else market_data.get_quotes(symbol,'US_STOCK',depth=1))
            received=clock();status=getattr(response,'status_code',200)
            if type(status) is int and 400<=status<=599:
                item.update(state='READ_FAILED',diagnostics={'http_status':status})
            else:
                body=_response_value(response)
                item.update(state='RESPONSE_RECEIVED',received_at=received.isoformat(),
                            inspection=describe(body,received))
        except Exception as exc:item.update(state='READ_FAILED',diagnostics=safe_exception_details(exc))
        finally:
            close=getattr(response,'close',None)
            if callable(close):
                try:close()
                except Exception:pass
        item['latency_ms']=round((timer()-started)*1000,3);records.append(item)
        if attempts<len(plan):wait(1)
    return dict(scope='READ_ONLY_EQUITY_QUOTE_SOURCE_COMPARISON_V1',market_data_attempts=attempts,
        orders=0,grok_requests=0,settings_modified=False,execution_authorized=False,records=records,
        rounds=rounds,source_summary=source_summary(records),
        limitations=['Selected symbols and samples only; not proof of continuous execution readiness',
                     'A received response does not establish data entitlements or executable BBO',
                     'Timestamp field/unit interpretations require provider contract review',
                     'No raw response, market prices, credentials or account data exported'])


def main():
    import argparse,os
    from dotenv import dotenv_values
    from app.configuration.loader import load_configuration
    from app.webull.sdk_market_data import create_official_data_client
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    select=p.add_mutually_exclusive_group()
    select.add_argument('--symbols',nargs='+')
    select.add_argument('--checkpoint-directory',type=Path)
    p.add_argument('--rounds',type=int,choices=(1,2),default=1)
    p.add_argument('--read',action='store_true');args=p.parse_args()
    if not args.read:p.error('--read required; nothing requested')
    repo=args.repo.resolve();output=args.output.resolve()
    if output.exists():p.error('Use a new output path')
    try:
        os.chdir(repo);c=load_configuration({**dotenv_values(repo/'.env'),**os.environ})
        if c.environment.value!='PAPER' or c.live_trading_enabled or c.grok_paper_entries_enabled:
            raise ValueError('Unsafe configuration')
        m=c.market_data
        if m is None or m.api_base_url.rstrip('/')!='https://api.webull.com' or m.environment.value!='PRODUCTION':
            raise ValueError('Production market-data configuration required')
        if not m.api_key or not m.api_secret:raise ValueError('Local credentials missing')
        selection=(select_recent_rejections(args.checkpoint_directory,now=datetime.now(UTC))
                   if args.checkpoint_directory else None)
        symbols=validated_symbols(args.symbols or SYMBOLS) if selection is None else tuple(selection['symbols'])
        if symbols:
            client=create_official_data_client(app_key=m.api_key,app_secret=m.api_secret,
                endpoint=m.api_base_url,timeout_seconds=10)
            report=run(client.market_data,symbols=symbols,rounds=args.rounds)
        else:
            report=dict(scope='READ_ONLY_EQUITY_QUOTE_SOURCE_COMPARISON_V1',market_data_attempts=0,
                orders=0,grok_requests=0,settings_modified=False,execution_authorized=False,records=[])
        report['selection']=selection or dict(status='EXPLICIT_SYMBOLS' if args.symbols else 'LIQUID_CONTROLS')
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    except Exception:
        print('QUOTE_PROBE=LOCAL_SETUP_OR_OUTPUT_FAILED');return 2
    print('QUOTE_PROBE=COMPLETED_REVIEW_REQUIRED');print('UPLOAD_SUMMARY='+str(output));return 0


if __name__=='__main__':raise SystemExit(main())
