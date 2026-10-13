from copy import deepcopy
import json
import sqlite3

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok import GrokClient, GrokUnavailable
from app.asset_modules.grok_learning import GrokLearningLoop, LearningJournal, main
from app.asset_modules.grok_learning_check import check


def experiment():
    data = {"version": "ENGINE_COMPARISON_V1", "experiment_id": "trial-v1",
            "dataset_id": "same-quotes", "cost_model_id": "spread-fees-v1",
            "evidence_kind": "QUOTE_PROXY", "engines": {}, "results": []}
    for engine in EngineId:
        data["engines"][engine.value] = {"champion": "baseline-v1",
                                       "policies": ["baseline-v1", "candidate-v1"]}
        for policy, pnl in [("baseline-v1", "-5"), ("candidate-v1", "10")]:
            data["results"].append(dict(engine=engine.value, policy=policy,
                episode_id="one", instrument="SPY", trading_date="2026-10-09",
                status="CLOSED", net_pnl=pnl))
    return data


class Client:
    def __init__(self, answer=None):
        self.answer, self.calls = answer, []

    def request(self, role, instruction, context, schema):
        self.calls.append((role, context))
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        return {"candidate_ids": [context["candidates"][0]["id"]],
                "reason": "Nominate a future trial; this one paired episode is insufficient proof."}


def test_offline_preview_all_engines_survives_restart_and_preserves_input(tmp_path):
    data = experiment()
    original = deepcopy(data)
    path = tmp_path / "learning.sqlite3"
    journal = LearningJournal(path)
    journal.ingest(data)
    preview = journal.review("trial-v1")
    assert preview["state"] == "REVIEW_NOT_REQUESTED"
    assert {c["engine"] for c in preview["candidates"]} == {e.value for e in EngineId}
    assert data == original
    assert LearningJournal(path).review("trial-v1") == preview
    journal.ingest(data)  # exact reimport is idempotent
    data["cost_model_id"] = "different-costs"
    with pytest.raises(ValueError, match="identity conflict"):
        journal.ingest(data)


def test_review_once_persists_evidence_and_does_not_activate(tmp_path):
    path = tmp_path / "learning.sqlite3"
    journal = LearningJournal(path)
    journal.ingest(experiment())
    client = Client()
    result = journal.review("trial-v1", client=client)
    assert client.calls[0][0] == "LEARNING"
    assert client.calls[0][1]["comparison"]["engines"][0]["paired_closed_episodes"] == 1
    assert result["state"] == "PAPER_TRIAL_NOMINATED"
    assert result["promotion"] == "NONE"
    assert LearningJournal(path).review("trial-v1", client=client) == result
    assert len(client.calls) == 1


@pytest.mark.parametrize("answer", [
    {"candidate_ids": ["invented"], "reason": "x"},
    {"candidate_ids": [], "reason": "x", "execute": True},
    {"candidate_ids": [], "reason": ""},
    {"candidate_ids": [{}], "reason": "x"},
    {"candidate_ids": "anything", "reason": "x"},
    GrokUnavailable("secret provider details"),
])
def test_invalid_or_failed_reviews_revoke_selection_and_do_not_retry(tmp_path, answer):
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest(experiment())
    client = Client(answer)
    result = journal.review("trial-v1", client=client)
    assert result["state"] == "REVIEW_FAILED"
    assert result["selected"] == []
    assert "secret" not in json.dumps(result)
    assert journal.review("trial-v1", client=client) == result
    assert len(client.calls) == 1


def test_duplicate_selection_rejected(tmp_path):
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest(experiment())
    candidate = journal.review("trial-v1")["candidates"][0]["id"]
    result = journal.review("trial-v1", client=Client(
        {"candidate_ids": [candidate, candidate], "reason": "x"}))
    assert result["state"] == "REVIEW_FAILED"


@pytest.mark.parametrize("pnl,status", [("-1", "CLOSED"), ("-6", "CLOSED"), (None, "UNRESOLVED")])
def test_losing_or_unpaired_policies_never_nominate_or_call_model(tmp_path, pnl, status):
    data = experiment()
    for row in data["results"]:
        if row["policy"] == "candidate-v1":
            row.update(net_pnl=pnl, status=status)
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest(data)
    client = Client()
    result = journal.review("trial-v1", client=client)
    assert result["state"] == "NO_ELIGIBLE_CHALLENGER"
    assert result["candidates"] == []
    assert not client.calls


def test_crashed_inflight_review_is_not_repeated(tmp_path):
    path = tmp_path / "learning.sqlite3"
    journal = LearningJournal(path)
    journal.ingest(experiment())
    class Crash(Client):
        def request(self, *args):
            raise RuntimeError("process interrupted")
    with pytest.raises(RuntimeError):
        journal.review("trial-v1", client=Crash())
    client = Client()
    assert LearningJournal(path).review("trial-v1", client=client)["state"] == "REQUESTED"
    assert not client.calls


def test_unrelated_database_is_preserved(tmp_path):
    path = tmp_path / "paper.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE orders (id TEXT)")
    with pytest.raises(ValueError, match="dedicated"):
        LearningJournal(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("orders",)]


def test_learning_role_uses_existing_bounded_transport(tmp_path):
    def transport(request):
        payload = json.loads(request.data)
        context = json.loads(payload["input"][1]["content"])
        answer = {"candidate_ids": [], "reason": "Insufficient independent evidence."}
        assert "tools" not in payload
        assert "key" not in json.dumps(context)
        return json.dumps({"status": "completed", "output": [{"type": "message",
            "role": "assistant", "status": "completed", "content": [{
            "type": "output_text", "text": json.dumps(answer)}]}]}).encode()
    client = GrokClient("key", "grok-test", transport=transport)
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest(experiment())
    assert journal.review("trial-v1", client=client)["state"] == "HOLD"
    assert client.requests == 1


def test_cli_is_offline_even_with_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XAI_API_KEY", "do-not-call")
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(experiment()))
    assert main([str(path), "--journal", str(tmp_path / "learning.sqlite3")]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "REVIEW_NOT_REQUESTED"


def test_feedback_keeps_prior_reviews_separate_from_new_evidence(tmp_path):
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    data = experiment()
    journal.ingest(data)
    journal.review("trial-v1", client=Client())
    data["experiment_id"] = "trial-v2"
    data["dataset_id"] = "new-frozen-dataset"
    journal.ingest(data)
    client = Client()
    journal.review("trial-v2", client=client)
    context = client.calls[0][1]
    assert context["prior_reviews_not_independent_evidence"][0]["experiment_id"] == "trial-v1"
    assert context["comparison"]["dataset_id"] == "new-frozen-dataset"
    assert context["comparison"]["engines"][0]["paired_closed_episodes"] == 1


def test_synthetic_high_win_rate_loser_not_selected(tmp_path):
    assert check(tmp_path)["passed"]


def test_loop_consumes_new_experiments_once_and_then_idles(tmp_path):
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    client = Client()
    loop = GrokLearningLoop(journal, client=client)
    assert loop.tick()["state"] == "IDLE"
    for name in ("one", "two"):
        data = experiment()
        data["experiment_id"] = name
        journal.ingest(data)
        assert loop.tick()["experiment_id"] == name
        assert loop.tick()["state"] == "IDLE"
    assert len(client.calls) == 2


def test_explicit_loop_wait_is_interruptible_and_stopped_loop_makes_no_call(tmp_path):
    from threading import Event
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest(experiment())
    client = Client()
    loop = GrokLearningLoop(journal, client=client)
    stopped = Event()
    stopped.set()
    loop.run(stopped, lambda _: pytest.fail("Stopped loop must not emit"))
    assert not client.calls
    class StopAfterOne:
        def is_set(self):
            return False
        def wait(self, seconds):
            assert seconds == 60
            return True
    emitted = []
    loop.run(StopAfterOne(), emitted.append)
    assert len(client.calls) == len(emitted) == 1
