from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from app.asset_modules.paper_day_setup import prepare


def test_manual_launchers_are_prepared_without_mutating_config_or_starting_anything(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();env=repo/'.env';env.write_text('PRIVATE_PLACEHOLDER=local-only')
    result=prepare(repo,tmp_path/'trial')
    assert env.read_text()=='PRIVATE_PLACEHOLDER=local-only'
    assert result['prepared_only'] and not result['atlas_started'] and result['requests']==0
    assert result['raw_journal_limit_bytes']==4*1024**3
    atlas=(tmp_path/'trial/Start-Atlas-Paper-Trial.ps1').read_text()
    grok=(tmp_path/'trial/Start-Grok-Intentions.ps1').read_text()
    assert '-m app.gui.app' in atlas and 'Get-CimInstance' in atlas
    assert '^python(w)?\\.exe$' in atlas
    assert 'finally' in atlas and 'Pop-Location' in atlas
    assert '$TrialEnvironment' in atlas and '$Profile' not in atlas
    assert 'PRIVATE_PLACEHOLDER' not in atlas+grok
    assert 'grok_day_proposals' in grok
    assert "'ATLAS_GROK_PAPER_ENTRIES_ENABLED' = 'false'" in atlas
    assert not (tmp_path/'trial/experiment.json').exists()


def test_existing_output_and_low_disk_space_are_rejected(tmp_path,monkeypatch):
    import app.asset_modules.paper_day_setup as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
    with pytest.raises(ValueError,match='FREE_SPACE'):prepare(tmp_path,tmp_path/'trial')
    assert not (tmp_path/'trial').exists()
