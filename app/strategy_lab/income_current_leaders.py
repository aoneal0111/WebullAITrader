"""Historical diagnostics for current-window leaders; explicitly selected in hindsight."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from app.strategy_lab.daily_income_lab import Spec,load_protocol,load_frames,pack,simulate,metrics,write


def run(root,current,historical):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    current=Path(current);data=json.loads(current.read_text());p=load_protocol(historical)
    chosen={}
    for tf,item in data['timeframes'].items():
        for capital in (10000.,100000.):
            row=max((r for r in item['rows'] if r['initial_cash']==capital and r['fee_per_side']==.004),
                    key=lambda r:r['average_net_per_day'])
            chosen[(tf,row['name'])]=row
    plan=dict(scope='HINDSIGHT_CURRENT_LEADERS_HISTORICAL_DIAGNOSTIC_V1',
       current_review_sha256=sha256(current.read_bytes()).hexdigest(),
       selections=[dict(timeframe=tf,spec=r['spec']) for (tf,_),r in chosen.items()],
       selected_using_current_results=True,qualification_eligible=False,orders=0)
    write(root/'plan.json',plan);rows=[];frames={}
    for (tf,name),selected in chosen.items():
        if tf not in frames:frames[tf]=load_frames(p,tf)
        spec=Spec(**selected['spec'])
        for stage in ('development','validation','assessment'):
            packed=pack(frames[tf],p,tf,stage,spec)
            for fee in (.004,.008):
                for capital in p['capital_scenarios']:
                    r=simulate(packed,spec,tf,fee,capital,.0005,6)
                    rows.append(dict(name=name,timeframe=tf,stage=stage,spec=asdict(spec),fee_per_side=fee,
                                     **metrics(r,capital,2.,100.)))
        print('CURRENT_LEADER_CHECK',tf,name,flush=True)
    write(root/'review.json',dict(**plan,runs=len(rows),rows=rows))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--current',type=Path,required=True);p.add_argument('--historical',type=Path,required=True)
    a=p.parse_args();run(a.root,a.current,a.historical)


if __name__=='__main__':main()
