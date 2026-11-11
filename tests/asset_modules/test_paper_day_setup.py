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
    # Both processes must bind the same explicit engine authorization profile.
    for launcher in (atlas,grok):
        assert "'PAPER_SYMBOL_AUTHORIZATION_MODE' = 'DYNAMIC_WARRIOR_AND_QUICK_SCALPER'" in launcher
    assert not (tmp_path/'trial/experiment.json').exists()


def test_existing_output_and_low_disk_space_are_rejected(tmp_path,monkeypatch):
    import app.asset_modules.paper_day_setup as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
    with pytest.raises(ValueError,match='FREE_SPACE'):prepare(tmp_path,tmp_path/'trial')
    assert not (tmp_path/'trial').exists()


def test_team_launchers_are_manual_paper_only_and_have_readiness_report(tmp_path,monkeypatch):
    monkeypatch.setenv('XAI_API_KEY','fixture-only')
    monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')
    repo=tmp_path/'repo';repo.mkdir()
    result=prepare(repo,tmp_path/'trial',profile='CENT_EXECUTABLE_V3',teams=True)
    root=tmp_path/'trial'
    atlas=(root/'Start-Atlas-Paper-Trial.ps1').read_text()
    team=(root/'Start-Grok-Teams.ps1').read_text()
    assert '--teams' in team and team==(root/'Start-Grok-Intentions.ps1').read_text()
    assert "'ATLAS_GROK_EXECUTION_TRIAL' = ''" in atlas and "'LIVE_TRADING_ENABLED' = 'false'" in atlas
    assert not (root/'experiment.json').exists()
    assert 'paper_morning_status' in (root/'Show-Morning-Status.ps1').read_text()
    assert result['shared_paid_attempt_cap']==60 and not result['crypto_nfi_connected']
    assert not result['credentials_validated_online']
    assert 'fixture-only' not in ''.join(p.read_text() for p in root.iterdir())


def test_explicit_full_day_launcher_uses_24_hours_without_starting_or_renewing(tmp_path,monkeypatch):
    monkeypatch.setenv('XAI_API_KEY','fixture-only')
    monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')
    repo=tmp_path/'repo';repo.mkdir()
    result=prepare(repo,tmp_path/'trial',profile='GROK_WARRIOR_ACCOUNT_V6',hours=24,clock_tolerance=True,extended_sessions=True)
    script=(tmp_path/'trial/Start-Atlas-Paper-Trial.ps1').read_text()
    assert '--hours 24' in script and '24 HOURS' in script
    assert result['entry_window_hours']==24 and result['max_recording_hours']==24
    assert not (tmp_path/'trial/experiment.json').exists()
