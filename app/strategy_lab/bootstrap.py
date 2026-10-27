"""One-hour, zero-Grok-request bootstrap of the existing virtual-money lab.

Only renews the existing entry lease. Native signals, positions, fills, stops
and targets remain owned by the running dry-run container.
"""
import argparse
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import sqlite3
import time

from .supervisor import verify,evidence,write_control

MAX_SECONDS=3600


def refusal(data):
    if data.get('status')!='DRY_RUN_LEDGER_READ':return 'NATIVE_LEDGER_NOT_READY'
    if data.get('unreconciled_closed_trades',0):return 'UNRECONCILED_NATIVE_LEDGER'
    if data['realized_net']<=-20:return 'REALIZED_LOSS_LIMIT'
    if data['open_positions']>2 or data['open_stake']>100.01:return 'EXPOSURE_LIMIT'
    return None


def run(root,*,new_trial=False,clock=time.time,timer=time.monotonic,wait=time.sleep,progress=print):
    root=Path(root);m=verify(root);lab_id=m['lab_id']
    # Same lock as the Grok manager: the two never control permission together.
    lock=root/'manager.lock'
    try:
        with lock.open('x',encoding='utf-8') as handle:handle.write(str(os.getpid()))
    except FileExistsError:raise ValueError('MANAGER_ALREADY_RUNNING') from None
    started=timer();wall_started=clock();status='STARTING';initial=None;latest=None
    try:
        # Old permission is revoked even if initial checks fail.
        write_control(root,lab_id,'PAUSE',clock())
        initial=latest=evidence(root)
        reason=refusal(initial)
        if reason:status=reason
        elif initial['open_positions']!=0 or initial['open_stake']!=0:status='FLAT_START_REQUIRED'
        elif initial['closed_trades']>=3:status='SAMPLE_ALREADY_AVAILABLE'
        elif (root/'user_data/PAUSE_REQUESTED').exists() and not new_trial:status='MANUAL_PAUSE'
        else:
            if new_trial:(root/'user_data/PAUSE_REQUESTED').unlink(missing_ok=True)
            while True:
                elapsed=timer()-started;now=clock()
                if not 0<=elapsed<MAX_SECONDS:status='TIME_LIMIT';break
                if abs((now-wall_started)-elapsed)>2:status='CLOCK_DISCONTINUITY';break
                verify(root)
                latest=evidence(root)
                reason=refusal(latest)
                if reason:status=reason;break
                if (root/'user_data/PAUSE_REQUESTED').exists():status='MANUAL_PAUSE';break
                if latest['closed_trades']>=3:status='INITIAL_SAMPLE_COLLECTED';break
                applied=write_control(root,lab_id,'ALLOW',now,
                    lease_seconds=min(90,MAX_SECONDS-elapsed))
                if applied!='ALLOW':status='MANUAL_PAUSE';break
                status='BOOTSTRAP_RUNNING'
                report={'scope':'BOUNDED_NATIVE_DRY_RUN_BOOTSTRAP_V1','status':status,
                    'elapsed_seconds':round(elapsed,3),'grok_requests':0,
                    'controller_orders':0,'atlas_baseline_modified':False,'evidence':latest}
                (root/'private-bootstrap-progress.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                if progress:progress('BOOTSTRAP=RUNNING; NATIVE_ENTRY_PERMISSION=ALLOW; GROK_REQUESTS=0',flush=True)
                # The controller only monitors/renews; no quote or model calls.
                wait(min(30,MAX_SECONDS-elapsed))
    except KeyboardInterrupt:status='OPERATOR_STOP'
    except (ValueError,OSError,sqlite3.Error):status='CHECK_FAILED_OR_REVOKED'
    finally:
        try:write_control(root,lab_id,'PAUSE',clock())
        finally:lock.unlink(missing_ok=True)
        report={'scope':'BOUNDED_NATIVE_DRY_RUN_BOOTSTRAP_SUMMARY_V1','status':status,
            'elapsed_seconds':round(max(0,timer()-started),3),'grok_requests':0,
            'controller_orders':0,'atlas_baseline_modified':False,'entries_paused':True,
            'native_bot_must_remain_running_for_protective_exits':True,
            'initial_evidence':initial,'final_evidence':latest,
            'limitations':['Native simulated trades require actual strategy signals; no forced fills',
                           'Closed-trade sample does not establish profitability',
                           'Open unrealized PnL is unknown; existing native stops remain active',
                           'Controller loss/exposure checks run every 30 seconds, not atomically with native fills',
                           'Expired/crashed controller leases stop new entries within at most 90 seconds']}
        (root/'bootstrap-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lab',type=Path,required=True);p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--bootstrap',action='store_true');p.add_argument('--new-trial',action='store_true')
    a=p.parse_args()
    if not a.bootstrap:p.error('Explicit --bootstrap required for the bounded virtual-money experiment')
    from dotenv import dotenv_values
    env={**dotenv_values(a.repo/'.env'),**os.environ}
    if (env.get('WEBULL_TRADING_ENVIRONMENT','').upper()!='PAPER'
            or env.get('LIVE_TRADING_ENABLED','false').lower() not in ('false','0','')
            or env.get('ATLAS_GROK_PAPER_ENTRIES_ENABLED','false').lower() not in ('false','0','')):
        p.error('Existing safe Atlas PAPER flags required; no settings changed')
    r=run(a.lab,new_trial=a.new_trial)
    print('BOOTSTRAP_STATUS='+r['status'],flush=True)
    print('UPLOAD_SUMMARY='+str(a.lab/'bootstrap-summary.json'),flush=True)


if __name__=='__main__':main()
