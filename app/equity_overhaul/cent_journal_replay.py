"""Replay a closed Atlas journal through fixed cent-scalp research cases."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timedelta
from decimal import Decimal as D
from hashlib import sha256
import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.asset_modules.daily_research_journal import canonical,verify_journal,MAX_LINE
from app.market.calendar import market_session
from .cent_scalper import Quote
from .cent_portfolio import Portfolio,Protocol
from app.asset_modules.scalper_evidence_audit import typed
from app.strategies.warrior_momentum.quick_scalper import QuickScalpSnapshot

PROOF_SOURCE='app/strategies/warrior_momentum/quick_scalper_runtime.py'


def frozen_protocol():
    return dict(scope='FROZEN_CENT_PORTFOLIO_QUOTE_PROTOCOL_V1',
        cases=[asdict(Protocol(target=target,slippage=slip)) for target in (D('.05'),D('.10')) for slip in (D('.005'),D('.01'))],
        hypothesis='Three rising bid and ask updates, 1-3 seconds; enter only on a later provider quote',
        state='Source-verified two-second native eligibility proof; halt/state events revoke leases; unknown blocks entry',
        universe='All observed subscribed symbols with ask>=1, spread<=.01, and displayed size/cash; no Warrior setup/HOD/gain gate',
        exits='Cross fresh bid at net target, five-cent price stop, 60s hold, or session change; partial fills supported',
        selection='NONE; report all four predefined cases; no tuning or automatic promotion',
        fee_assumption='Unverified .0035 per share per side; slippage .005 base/.01 stress; no extra spread fee',
        orders=0,network_requests=0,grok_requests=0,activation_authorized=False)


def run(session,output):
    root=Path(session).resolve();output=Path(output).resolve()
    manifest=json.loads((root/'research-manifest.json').read_text())
    summary=json.loads((root/'research-summary.json').read_text())
    journal=verify_journal(root/'journal')
    if not journal['evidence_complete']:raise ValueError('COMPLETE_JOURNAL_REQUIRED')
    if (manifest.get('scope')!='NORMAL_DESKTOP_DAILY_RESEARCH_V1' or summary.get('policy_unchanged') is not True
            or manifest['baseline_sha256']!=journal['baseline_sha256']):raise ValueError('FROZEN_RECORDED_BASELINE_REQUIRED')
    source=Path(__file__).resolve().parents[2]
    if sha256((source/PROOF_SOURCE).read_bytes()).hexdigest()!=manifest['source_files'].get(PROOF_SOURCE):
        raise ValueError('NATIVE_ELIGIBILITY_PROOF_SOURCE_MISMATCH')
    output.mkdir(parents=True,exist_ok=False)
    protocol=frozen_protocol()
    (output/'frozen-protocol.json').write_text(json.dumps(protocol,default=str,indent=2),encoding='utf-8')
    portfolios=[Portfolio(Protocol(target=target,slippage=slip)) for target in (D('.05'),D('.10')) for slip in (D('.005'),D('.01'))]
    eligibility={};halted=set();counts=Counter();sessions=Counter();previous='0'*64;sequence=0;last_tick=None
    date=None;wall=None;monotonic=None;clock_fault=False;state_sources_verified=False
    for path in sorted((root/'journal').glob('events-*.jsonl')):
        with path.open('rb') as stream:
            while line:=stream.readline(MAX_LINE+1):
                if len(line)>MAX_LINE or not line.endswith(b'\n'):raise ValueError('JOURNAL_ROW_BOUND')
                row=json.loads(line);claimed=row.pop('sha256');sequence+=1
                if row['sequence']!=sequence or row['previous_sha256']!=previous or sha256(canonical(row)).hexdigest()!=claimed:
                    raise ValueError('JOURNAL_CHANGED_DURING_REPLAY')
                previous=claimed;now=datetime.fromisoformat(row['received_at']);tick=D(str(row['received_monotonic']))
                if now.utcoffset() is None or not tick.is_finite():raise ValueError('CAUSAL_CLOCK_REQUIRED')
                if last_tick is not None and tick<last_tick:raise ValueError('MONOTONIC_CLOCK_REGRESSED')
                last_tick=tick
                if wall is not None and abs(D(str((now-wall).total_seconds()))-(tick-monotonic))>D('.25'):
                    clock_fault=True;counts['clock_discontinuity']+=1
                    for engine in portfolios:
                        for symbol in tuple(engine.pending):engine.invalidate(symbol)
                wall,monotonic=now,tick
                for engine in portfolios:engine.last_tick=tick
                session_name=market_session(now).value
                local_date=now.astimezone(ZoneInfo('America/New_York')).date()
                if date is not None and local_date!=date:raise ValueError('SINGLE_TRADING_DATE_REQUIRED')
                date=local_date;p=row['payload']
                if row['kind']=='MARKET_ELIGIBILITY':
                    if not state_sources_verified:
                        for name in ('app/strategies/warrior_momentum/desktop_sidecar.py',
                                     'app/asset_modules/grok_observer_runtime_tap.py'):
                            if sha256((source/name).read_bytes()).hexdigest()!=manifest['source_files'].get(name):
                                raise ValueError('MARKET_STATE_PROOF_SOURCE_MISMATCH')
                        state_sources_verified=True
                    at=datetime.fromisoformat(p['known_at']);symbol=p['symbol']
                    if (p.get('source')!='NATIVE_SCANNER_OBSERVATION' or type(p.get('tradable')) is not bool
                            or type(p.get('halted')) is not bool or at.utcoffset() is None):
                        raise ValueError('EXPLICIT_TYPED_MARKET_STATE_REQUIRED')
                    if p['halted']:
                        halted.add(symbol)
                        for engine in portfolios:
                            engine.invalidate(symbol);engine.unmarkable.add(symbol)
                    if p['tradable'] and not p['halted'] and at<=now<=at+timedelta(seconds=2):
                        eligibility[symbol]=at+timedelta(seconds=2);counts['market_state_heartbeats']+=1
                    else:eligibility.pop(symbol,None)
                    continue
                if row['kind']=='STREAM_ASSESSMENT':
                    # This native function returns no snapshot when tradable is
                    # false or halted. Its recorded snapshot is an eligibility
                    # observation only, not a candidate entry or profit signal.
                    snap=typed(QuickScalpSnapshot,p['snapshot']);at=snap.decision_at
                    if snap.setup_type=='QUICK_SCALP_STREAM' and at<=now<=at+timedelta(seconds=2):
                        if len(eligibility)>=10000 and snap.symbol not in eligibility:raise ValueError('SYMBOL_BOUND_EXCEEDED')
                        eligibility[snap.symbol]=at+timedelta(seconds=2);counts['native_eligibility_proofs']+=1
                    continue
                if row['kind']!='MARKET_EVENT':continue
                symbol=p['symbol'];kind=p['event_type'];data=p['payload']
                if not symbol:continue
                if kind=='TRADING_HALT':
                    halted.add(symbol);eligibility.pop(symbol,None);counts['halt_events']+=1
                    for engine in portfolios:
                        engine.invalidate(symbol);engine.unmarkable.add(symbol)
                    continue
                if kind in ('MARKET_STATUS','RESUME','SESSION_CHANGE'):
                    # Resume removes a known halt, but does not itself establish
                    # account tradability. A new native proof is still required.
                    if kind=='RESUME':halted.discard(symbol)
                    eligibility.pop(symbol,None);counts['state_changes']+=1
                    for engine in portfolios:engine.invalidate(symbol)
                    continue
                if kind not in ('QUOTE','BOOK_SNAPSHOT'):
                    counts['non_book_market_events']+=1;continue
                try:
                    if kind=='QUOTE':bid,ask,bs,az=(D(data[k]) for k in ('bid','ask','bid_size','ask_size'))
                    else:
                        bid=D(data['bids'][0]['price']);ask=D(data['asks'][0]['price'])
                        bs=D(data['bids'][0]['size']);az=D(data['asks'][0]['size'])
                    provider=datetime.fromisoformat(p['timestamp'])
                    q=Quote(symbol,now,provider,bid,ask,bs,az,session_name=='CORE')
                except (ValueError,KeyError,IndexError,TypeError):
                    counts['invalid_or_missing_sized_book']+=1;continue
                allowed=not clock_fault and symbol not in halted and now<=eligibility.get(symbol,now-timedelta(seconds=1))
                counts['book_quotes']+=1;sessions[session_name]+=1
                counts['book_with_causal_native_eligibility' if allowed else 'book_without_current_eligibility']+=1
                for engine in portfolios:engine.feed(q,tick,sequence,eligible=allowed,session=session_name,halted=symbol in halted)
    if previous!=journal['chain_head'] or sequence!=journal['records']:raise ValueError('JOURNAL_CHANGED_DURING_REPLAY')
    cases=[]
    for index,engine in enumerate(portfolios):
        result=engine.result();trades=result.pop('trades')
        path=output/f'private-trades-case-{index+1}.json'
        path.write_text(json.dumps(trades,default=str,indent=2),encoding='utf-8')
        result.update(trades_file_sha256=sha256(path.read_bytes()).hexdigest(),trade_examples=trades[:10])
        cases.append(dict(rules=asdict(engine.rules),result=result))
    report=dict(scope='OFFLINE_SHARED_WALLET_CENT_SCALPER_REPLAY_V1',journal=journal,
        protocol_sha256=sha256((output/'frozen-protocol.json').read_bytes()).hexdigest(),
        implementation_sha256=sha256(Path(__file__).with_name('cent_portfolio.py').read_bytes()).hexdigest(),
        native_eligibility_source_verified=True,market_state_sources_verified=state_sources_verified,input_counts=dict(counts),quotes_by_session=dict(sessions),
        recorded_baseline_entry_diagnostics=summary.get('entry_diagnostics'),
        cases=cases,
        promotion='NONE',baseline_modified=False,orders=0,network_requests=0,grok_requests=0,
        limitations=['Retrospective short single-date sample, no independent validation or promotion',
            'Only subscribed observed stocks; not proof of an independent liquid-stock universe',
            'Eligibility limited to source-verified native stream evidence; unknown tradability blocks entries',
            'Hypothetical displayed-book fills; no broker latency, depth consumption or exchange queue simulation',
            'Fees/slippage are fixed research assumptions, not verified operator account costs',
            'Past rising quotes are a signal, never proof of remaining upside',
            'Open/partially closed positions remain explicit; no fabricated period-end liquidation',
            'Marked drawdown only at jointly fresh observed books; unseen excursions and stale marks remain unknown',
            'Native recorded assessments and candidate trades use different eligibility/risk paths and are not interchangeable'])
    (output/'cent-portfolio-summary.json').write_text(json.dumps(report,default=str,indent=2),encoding='utf-8')
    print('CENT_REPLAY=COMPLETE; PROMOTION=NONE; ORDERS=0')
    print('UPLOAD_SUMMARY='+str(output/'cent-portfolio-summary.json'))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();run(args.session,args.output)


if __name__=='__main__':main()
