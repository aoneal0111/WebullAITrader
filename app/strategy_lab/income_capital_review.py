"""Recheck historically selected finalists at each capital level, without reselection."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from app.strategy_lab.daily_income_lab import Spec,load_protocol,load_frames,pack,simulate,metrics,write
from app.strategy_lab import equity_income_study as equity


def run(root,crypto_root,equity_root):
    root=Path(root);root.mkdir(exist_ok=False,parents=True)
    rows=[]
    for market,folder in [('crypto',Path(crypto_root)),('equity',Path(equity_root))]:
        selection=json.loads((folder/'selection.json').read_text())['selected']
        if market=='crypto':p=load_protocol(folder);frames={tf:load_frames(p,tf) for tf in ('1h','4h')}
        else:p,frames=equity.load(folder)
        for index,item in enumerate(selection):
            spec=Spec(**item['spec']);tf=item.get('timeframe','1d')
            packed=pack(frames[tf],p,tf,'development',spec) if market=='crypto' else equity.pack(frames,p,'development',spec)
            for fee in (p['fee_per_side'] if market=='crypto' else p['fees_per_side']):
                for capital in p['capital_scenarios']:
                    result=simulate(packed,spec,tf,fee,capital,.0005,6) if market=='crypto' else equity.simulate(packed,spec,p,capital,fee)
                    rows.append(dict(market=market,name=spec.name,spec=asdict(spec),timeframe=tf,
                        fee_per_side=fee,**metrics(result,capital,2.,100.)))
            print('CAPITAL_DEVELOPMENT',market,index+1,flush=True)
        write(root/(market+'.json'),dict(stage='capital_development',rows=[r for r in rows if r['market']==market],
            selection_changed=False,orders=0))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--crypto',type=Path,required=True);p.add_argument('--equity',type=Path,required=True)
    a=p.parse_args();run(a.root,a.crypto,a.equity)


if __name__=='__main__':main()
