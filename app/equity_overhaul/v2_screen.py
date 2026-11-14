"""Small-output exploratory V2 screen of already captured history."""
from datetime import date
from pathlib import Path

from .validation import PLAN, SYMBOLS, fingerprint, load_dataset
from .local_audit import digest, read_json, source_matches, summary
from .research_v2 import prepare, trades, POLICIES, COSTS, experiment_plan, plan_hash
from decimal import Decimal as D


def run_screen(history, report_path, *, progress=print):
    history=Path(history)
    report=read_json(report_path,40_000_000);manifest=read_json(history/'manifest.json',2_000_000)
    original=report['plan_document']
    if (report.get('scope')!='OFFLINE_EQUITY_TEMPORAL_SCREEN' or
            original['plan']!=PLAN or original['plan_sha256']!=fingerprint(PLAN) or
            manifest['plan_document']!=original or not source_matches(original['source_hashes'])):
        raise ValueError('original evidence identity mismatch')
    data, provenance=load_dataset(history)
    if set(data)!=set(SYMBOLS) or provenance['manifest_sha256']!=report['data']['manifest_sha256'] or provenance['normalized_inputs']!=report['data']['normalized_inputs']:
        raise ValueError('captured history/report mismatch')
    ready=prepare(data);cells=[];aggregates=[]
    for window in PLAN['windows']:
        start=date.fromisoformat(window['start']);end=date.fromisoformat(window['end_exclusive'])
        for policy in POLICIES:
            for scenario,bps,fee in COSTS:
                records,diag=trades(ready,policy,D(bps),D(fee),start,end)
                for symbol in POLICIES[policy]['symbols']:
                    own=[t for t in records if t['symbol']==symbol]
                    cells.append({'seen_period':window['name'],'policy_id':policy,'symbol':symbol,
                                  'cost':scenario,**summary(own)})
                aggregates.append({'seen_period':window['name'],'policy_id':policy,'cost':scenario,
                                   **summary(records),'diagnostics':diag})
            progress('V2_PROGRESS='+window['name']+':'+policy,flush=True)
    files=('research_v2.py','v2_screen.py')
    return {'scope':'POST_V1_EXPLORATORY_EQUITY_V2','execution_authorized':False,'promotion':'NONE',
        'experiment_plan':experiment_plan(),'experiment_sha256':plan_hash(),
        'source_hashes':{n:digest(Path(__file__).with_name(n)) for n in files},
        'original_report_sha256':digest(report_path),'data':provenance,
        'results':cells,'aggregates':aggregates,
        'deployment_blockers':['NO_UNSEEN_VALIDATION','NO_QUOTE_VERIFIED_COSTS','NO_SHARED_CAPITAL_PORTFOLIO',
            'NO_PRODUCTION_ADAPTER','NO_FORWARD_PAPER_PARITY','NO_FULL_MARKET_POINT_IN_TIME_MOVERS',
            'NO_DIVIDEND_CORPORATE_ACTION_ADJUSTMENT'],
        'limitations':['New adaptations designed after V1 results; no author performance is reproduced',
            'The old holdout label is retained only as the original seen-period identifier',
            'Stop-market OHLC fills and same-bar stop precedence are assumptions, not verified executions',
            'Four current stocks cannot replicate a historical top20 out of 1000+ universe',
            'Relative opening volume is a research ranking, not a new production entry gate']}
