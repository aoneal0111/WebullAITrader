from dataclasses import asdict
from datetime import timedelta
import json
from hashlib import sha256

import pytest

from app.asset_modules.grok_observer_input import load_snapshots,observe_file
from tests.asset_modules.test_grok_observer import snap,fixture,BASE,NOW


def write(path,rows):path.write_text('\n'.join(json.dumps(r,default=str) for r in rows),encoding='utf-8')


def test_file_to_observer_is_read_only_and_preserves_owner_and_baseline(tmp_path):
    path=tmp_path/'snapshots.jsonl';write(path,[asdict(snap())]);before=sha256(path.read_bytes()).hexdigest()
    trial,requests,_=fixture();records=observe_file(path,trial,BASE)
    assert len(records)==1 and records[0].status=='OBSERVED' and not records[0].execution_authorized
    assert records[0].baseline_sha256==BASE and len(requests)==1
    assert sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('change',[dict(baseline_sha256='b'*64),dict(engine='CRYPTO'),dict(api_key='secret'),
    dict(bid_at=(NOW-timedelta(seconds=3)).isoformat()),dict(ask_at=(NOW+timedelta(seconds=1)).isoformat())])
def test_entire_input_is_validated_before_first_provider_call(tmp_path,change):
    path=tmp_path/'snapshots.jsonl';row=asdict(snap(event_id='second'));row.update(change)
    write(path,[asdict(snap()),row]);trial,requests,_=fixture()
    with pytest.raises((ValueError,TypeError)):observe_file(path,trial,BASE)
    assert requests==[]


def test_duplicate_keys_identities_order_and_bounds_are_rejected(tmp_path):
    path=tmp_path/'snapshots.jsonl';row=asdict(snap())
    write(path,[row,row])
    with pytest.raises(ValueError,match='duplicate'):load_snapshots(path,BASE)
    path.write_text('{"engine":"a","engine":"b"}')
    with pytest.raises(ValueError,match='duplicate'):load_snapshots(path,BASE)
    path.write_bytes(b'x'*1_000_001)
    with pytest.raises(ValueError,match='byte limit'):load_snapshots(path,BASE)
    write(path,[row]*101)
    with pytest.raises(ValueError,match='100'):load_snapshots(path,BASE)
