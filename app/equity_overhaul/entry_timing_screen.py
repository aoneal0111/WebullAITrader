"""Fixed pre-HOD entry/partial-exit comparison on already reviewed dates."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from .validation import PLAN, SYMBOLS, fingerprint, load_dataset
from .local_audit import read_json, source_matches, digest, summary
from .research_v2 import prepare, COSTS
from .early_momentum import POLICY, SPEC, specification, fingerprint as policy_hash, replay


def run_screen(history, report_path, *, progress=print):
    history=Path(history);report=read_json(report_path,40_000_000)
    original=report['plan_document'];manifest=read_json(history/'manifest.json',2_000_000)
    if (report.get('scope')!='OFFLINE_EQUITY_TEMPORAL_SCREEN' or original['plan']!=PLAN or
            original['plan_sha256']!=fingerprint(PLAN) or manifest['plan_document']!=original or
            not source_matches(original['source_hashes'])):
        raise ValueError('original evidence identity mismatch')
    data,provenance=load_dataset(history)
    if (set(data)!=set(SYMBOLS) or provenance['manifest_sha256']!=report['data']['manifest_sha256'] or
            provenance['normalized_inputs']!=report['data']['normalized_inputs']):
        raise ValueError('captured history/report mismatch')
    ready=prepare(data);aggregates=[];cells=[]
    for window in PLAN['windows']:
        start=date.fromisoformat(window['start']);end=date.fromisoformat(window['end_exclusive'])
        for mode in SPEC['management']:
            for cost,bps,fee in COSTS:
                records,diag=replay(ready,mode,D(bps),D(fee),start,end)
                extras=dict(partial_trades=sum(t['partial_taken'] for t in records),
                    partial_trades_finishing_nonpositive=sum(t['partial_taken'] and D(t['net_r'])<=0 for t in records))
                aggregates.append(dict(seen_period=window['name'],mode=mode,cost=cost,**summary(records),**extras,diagnostics=diag))
                for symbol in SYMBOLS:
                    own=[r for r in records if r['symbol']==symbol]
                    cells.append(dict(seen_period=window['name'],mode=mode,cost=cost,symbol=symbol,**summary(own)))
            progress('ENTRY_TIMING='+window['name']+':'+mode,flush=True)
    files=('early_momentum.py','entry_timing_screen.py','research_v2.py')
    return dict(scope='PRE_HOD_ENTRY_AND_PARTIAL_EXIT_EXPLORATORY_V1',policy_id=POLICY,owner=SPEC['owner'],
        specification=specification(),specification_sha256=policy_hash(),
        execution_authorized=False,promotion='NONE',data=provenance,original_report_sha256=digest(report_path),
        source_hashes={n:digest(Path(__file__).with_name(n)) for n in files},results=cells,aggregates=aggregates,
        limitations=['Already reviewed periods, including the old holdout label; no fresh holdout or automatic promotion',
            'Fixed six-symbol sample is not a historical small-cap movers universe; few qualifying 10-percent days may make it inadequate',
            'Resting intrabar stops are assumed fills; minute bars cannot verify latency, spread, liquidity or exact event order',
            'Half-position exits assume divisible shares; whole-share rounding/minimum per-order charges are not modeled',
            'No shared-capital sizing, verified quotes, independent replay reference or production adapter',
            'A 10-percent move is a watch condition, not a standalone buy signal; no RVOL or spread acceptance gate',
            'Partial profit does not guarantee a profitable full trade after remaining losses, gaps and costs'])
