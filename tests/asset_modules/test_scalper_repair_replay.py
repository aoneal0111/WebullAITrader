from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import pytest

from app.asset_modules.daily_research_journal import DailyJournal
from app.asset_modules.scalper_evidence_audit import POLICY_FILES, reconstructed_preview
from app.asset_modules.scalper_repair_replay import compare, PRIOR_SOURCE
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig, QuickScalperPolicy
from tests.warrior_momentum.test_quick_scalper import snapshot, NOW


def session(tmp_path, *, prior_schema=False, mismatch=False):
    root=tmp_path/'session';root.mkdir()
    source=Path(__file__).resolve().parents[2]
    hashes={p:sha256((source/p).read_bytes()).hexdigest() for p in POLICY_FILES}
    if prior_schema:hashes=dict(zip(POLICY_FILES,PRIOR_SOURCE))
    baseline='a'*64
    (root/'research-manifest.json').write_text(json.dumps(dict(scope='NORMAL_DESKTOP_DAILY_RESEARCH_V1',
        source_files=hashes,baseline_sha256=baseline)))
    (root/'research-summary.json').write_text(json.dumps(dict(policy_unchanged=True)))
    journal=DailyJournal(root/'journal',baseline,clock=lambda:NOW)
    config=QuickScalperConfig(enabled=True,cent_target_minimum=D('.05'),cent_target_maximum=D('.10'),
        cent_pullback_support_enabled=True,minimum_net_target_reward_r=D(1))
    raw=asdict(config)
    if prior_schema:raw.pop('cent_fill_anchored_economics')
    journal.submit('NATIVE_POLICY_BASELINE',dict(settings=dict(scalper=raw)))
    s=replace(snapshot(ask='5',bid='4.98',stop='4.92',short_range='.20'),
        setup_type='QUICK_SCALP_STREAM',support_source='CONFIRMED_MICRO_PULLBACK',
        support_timestamp=NOW-timedelta(seconds=2),observed_bid_advance=D('.20'),observed_ask_advance=D('.20'))
    r=QuickScalperPolicy(config).assess(s)
    journal.submit('STREAM_ASSESSMENT',dict(snapshot=s,reason='WRONG' if mismatch else r.reason,decision=r.decision))
    quote=dict(last='5',bid='4.98',ask='5',last_timestamp=NOW,
        bid_timestamp=NOW-timedelta(seconds=9),ask_timestamp=NOW-timedelta(seconds=9))
    journal.submit('ASSESSMENT',dict(engine='QUICK_SCALPER',preview=s,quote=quote,assessed_at=NOW,baseline_result='BLOCKED'))
    journal.close()
    return root


@pytest.mark.parametrize('prior_schema',[False,True])
def test_comparison_reconciles_exact_old_policy_and_never_retimes_stale_quotes(tmp_path,prior_schema):
    root=session(tmp_path,prior_schema=prior_schema)
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    result=compare(root)
    assert result['baseline_replay_matches']
    assert result['baseline_reasons']['confirmation']==result['fill_anchored_reasons']['confirmation']=={'PROVIDER_DATA_STALE':1}
    assert result['baseline_reasons']['stream']=={'CENT_TARGET_ECONOMICS_WAIT':1}
    assert result['fill_anchored_reasons']['stream']=={'POSITIVE_EXECUTABLE_EDGE':1}
    assert result['orders']==result['grok_requests']==result['network_requests']==0
    assert result['promotion']=='NONE'
    assert before=={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation',['source','summary','chain','reason'])
def test_no_comparison_on_unknown_source_changed_journal_or_baseline_mismatch(tmp_path,mutation):
    root=session(tmp_path,mismatch=mutation=='reason')
    if mutation=='source':
        p=root/'research-manifest.json';m=json.loads(p.read_text());m['source_files'][POLICY_FILES[0]]='f'*64;p.write_text(json.dumps(m))
    elif mutation=='summary':(root/'research-summary.json').write_text('{"policy_unchanged": false}')
    elif mutation=='chain':
        p=next((root/'journal').glob('events-*.jsonl'));p.write_bytes(p.read_bytes()[:-5])
    with pytest.raises(ValueError):compare(root)


def test_confirmation_details_survive_native_tap_journal_without_fresh_context(tmp_path):
    from app.asset_modules.grok_observer_capture import ContextCapture
    from app.asset_modules.grok_observer_runtime_tap import RuntimeContextTap
    journal=DailyJournal(tmp_path/'journal','a'*64,clock=lambda:NOW)
    capture=ContextCapture(tmp_path/'capture','a'*64,clock=lambda:NOW)
    tap=RuntimeContextTap(capture,'a'*64,journal=journal)
    try:
        assert tap.record_scalper_confirmation(dict(symbol='FAST',reason='PROVIDER_DATA_STALE',
            bid_age_seconds=D('9'),book_regressed=True))
    finally:
        tap.close();capture.close();journal.close()
    row=json.loads(next((tmp_path/'journal').glob('events-*.jsonl')).read_text().splitlines()[0])
    assert row['kind']=='SCALPER_CONFIRMATION_DIAGNOSTIC'
    assert row['payload']['book_regressed'] is True and row['payload']['bid_age_seconds']=='9'
