"""Four explicit read-only stock-data calls; never execution authorization."""
from datetime import UTC,datetime
import json
from pathlib import Path
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


def run(market_data,*,clock=lambda:datetime.now(UTC),timer=monotonic,wait=sleep):
    from app.webull.sdk_market_data import _response_value
    from app.webull.options_data_probe import safe_exception_details
    records=[];attempts=0
    for symbol in SYMBOLS:
        for operation in ('SNAPSHOT','DEPTH'):
            response=None;started=timer();attempts+=1
            item=dict(symbol=symbol,operation=operation)
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
            if attempts<4:wait(1)
    return dict(scope='READ_ONLY_EQUITY_QUOTE_SOURCE_COMPARISON_V1',market_data_attempts=attempts,
        orders=0,grok_requests=0,settings_modified=False,execution_authorized=False,records=records,
        limitations=['SPY/AAPL controls only; not proof of freshness for thin movers',
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
        client=create_official_data_client(app_key=m.api_key,app_secret=m.api_secret,
            endpoint=m.api_base_url,timeout_seconds=10)
        report=run(client.market_data)
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    except Exception:
        print('QUOTE_PROBE=LOCAL_SETUP_OR_OUTPUT_FAILED');return 2
    print('QUOTE_PROBE=COMPLETED_REVIEW_REQUIRED');print('UPLOAD_SUMMARY='+str(output));return 0


if __name__=='__main__':raise SystemExit(main())
