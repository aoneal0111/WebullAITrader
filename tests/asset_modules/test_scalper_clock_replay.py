from dataclasses import asdict,replace
from datetime import timedelta
from hashlib import sha256
import json
import zipfile
import pytest
from app.asset_modules.daily_research_journal import canonical,safe
from app.asset_modules.scalper_clock_replay import compare
from tests.warrior_momentum.test_quick_scalper import snapshot,NOW


def evidence(tmp_path,*,bad_reason=False,corrupt=False):
    baseline='f'*64
    source={'app/strategies/warrior_momentum/quick_scalper.py':'417a9b051bb4174fa68f71e8de6b5bb900ad0d8ebbd26ebac7439f78ec89b5dd',
        'app/strategies/warrior_momentum/quick_scalper_runtime.py':'6bb064fff42545cfa9b8ff6be52b3e0349d32fbc63ba43c252d919baceac73cd'}
    manifest=dict(live_trading_enabled=False,baseline_sha256=baseline,source_files=source,
        paper_experiment=dict(profile='GROK_WARRIOR_ACCOUNT_V6',trial_id='a'*32,entry_sessions=['PREMARKET','REGULAR','AFTER_HOURS']))
    manifest_raw=canonical(manifest)
    review=dict(trial_id='a'*32,verified_prefix={'baseline_sha256':baseline},files={
        'capture/research-manifest.json':{'sha256':sha256(manifest_raw).hexdigest()}})
    snap=safe(replace(snapshot(),last_timestamp=NOW+timedelta(milliseconds=130)))
    old=(NOW-timedelta(seconds=6)).isoformat()
    quote=dict(last='5',bid='4.96',ask='5',last_timestamp=old,bid_timestamp=old,ask_timestamp=old)
    payloads=[('STREAM_ASSESSMENT',dict(snapshot=snap,reason='WRONG' if bad_reason else 'PROVIDER_DATA_STALE')),
        ('ASSESSMENT',dict(engine='QUICK_SCALPER',preview=snap,quote=quote,assessed_at=NOW.isoformat(),baseline_result='BLOCKED'))]
    lines=[]
    for i,(kind,payload) in enumerate(payloads,1):
        row=dict(sequence=i,kind=kind,payload=payload,baseline_sha256=baseline)
        row['sha256']=sha256(canonical(row)).hexdigest();lines.append(canonical(row)+b'\n')
    raw=b''.join(lines);summary=dict(trial_id='a'*32,selected_records=2,selected_bytes=len(raw))
    if corrupt:raw=raw.replace(b'PROVIDER_DATA_STALE',b'PROVIDER_DATA_WRONG')
    path=tmp_path/'evidence.zip'
    with zipfile.ZipFile(path,'x') as z:
        z.writestr('research-manifest.json',manifest_raw);z.writestr('original-paper-day-review.json',canonical(review))
        z.writestr('entry-evidence-summary.json',canonical(summary));z.writestr('selected-events.jsonl',raw)
    return path


def test_replay_preserves_stale_confirmation_and_reports_only_policy_transitions(tmp_path):
    path=evidence(tmp_path);before=path.read_bytes();r=compare(path)
    assert r['baseline_reproduced'] and r['orders']==r['network_requests']==0
    assert r['transitions']['confirmation']=={'PROVIDER_DATA_STALE -> PROVIDER_DATA_STALE':1}
    assert not r['provider_timestamps_modified'] and not r['profitability_established']
    assert path.read_bytes()==before


@pytest.mark.parametrize('bad_reason,corrupt,expected',[(True,False,'BASELINE_CLOCK_REPLAY_MISMATCH'),(False,True,'SELECTED_RECORD_CHANGED')])
def test_changed_records_or_unreproduced_baseline_fail_closed(tmp_path,bad_reason,corrupt,expected):
    with pytest.raises(ValueError,match=expected):compare(evidence(tmp_path,bad_reason=bad_reason,corrupt=corrupt))
