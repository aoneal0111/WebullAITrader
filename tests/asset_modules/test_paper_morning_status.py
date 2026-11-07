import json
from app.asset_modules.paper_morning_status import report
import pytest


def test_read_only_status_does_not_create_trial_or_budget(tmp_path):
    result=report(tmp_path)
    assert not result['trial_started'] and result['grok_reserved_attempts']==0
    assert list(tmp_path.iterdir())==[]


def test_wrong_pointer_cannot_report_another_trial(tmp_path):
    (tmp_path/'experiment.json').write_text(json.dumps({'trial_id':'a','expires_at':'2026-11-03T22:00:00+00:00'}))
    (tmp_path/'recording-session.json').write_text(json.dumps({'trial_id':'b','session':'unread'}))
    with pytest.raises(ValueError,match='MISMATCH'):report(tmp_path)
