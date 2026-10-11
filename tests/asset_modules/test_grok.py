from datetime import UTC, datetime, timedelta
import json
from threading import Event, Thread

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok import GrokClient, GrokProposalProvider, GrokUnavailable, configured_client
from app.asset_modules.grok_coordinator import Candidate, GrokAdviser, GrokSuite, IntentKind, configured_suite
from app.asset_modules.supervisor import configured_provider

NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)


def response(answer, **changes):
    body = {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "status": "completed", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
    body.update(changes)
    return json.dumps(body).encode()


def test_grok_uses_one_fixed_endpoint_and_does_not_grant_tools():
    calls = []
    def transport(request):
        calls.append(request)
        return response({"proposals": []})
    client = GrokClient("test-key", "grok-test", transport=transport)
    assert not calls
    assert GrokProposalProvider(client).propose({"mode": "PAPER", "asset": "CRYPTO"}) == []
    request = calls[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.x.ai/v1/responses"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert payload["store"] is False
    assert "tools" not in payload and "previous_response_id" not in payload
    assert payload["text"]["format"]["strict"] is True
    assert "test-key" not in request.data.decode()


@pytest.mark.parametrize("raw", [b"not json", b"x"*65537,
    response({}, status="incomplete"), response({}, error={"message": "secret"}),
    response({}, output=[{"type": "function_call"}])],
    ids=["invalid-json", "oversized-response", "incomplete", "provider-error", "tool-call"])
def test_bad_responses_fail_closed_without_error_body(raw):
    client = GrokClient("secret", "grok-test", transport=lambda _: raw)
    with pytest.raises(GrokUnavailable, match="^GROK_REQUEST_FAILED$"):
        client.request("MASTER", "instruction", {"mode": "PAPER"}, {})


def test_failure_counts_and_cooldown_is_per_role():
    client = GrokClient("key", "grok-test", transport=lambda _: response({}), clock=lambda: 10)
    client.request("MASTER", "", {"mode": "PAPER"}, {})
    with pytest.raises(GrokUnavailable, match="COOLDOWN"):
        client.request("MASTER", "", {"mode": "PAPER"}, {})
    client.request("OPTIONS", "", {"mode": "PAPER"}, {})
    assert client.requests == 2
    client.requests = 120
    with pytest.raises(GrokUnavailable, match="SESSION_REQUEST_LIMIT"):
        client.request("FUTURES", "", {"mode": "PAPER"}, {})


def test_concurrency_is_bounded_and_busy_does_not_enqueue():
    entered, release = Event(), Event()
    def transport(_):
        entered.set()
        assert release.wait(2)
        return response({})
    client = GrokClient("key", "grok-test", transport=transport)
    errors = []
    def call(role):
        try:
            client.request(role, "", {"mode": "PAPER"}, {})
        except Exception as error:
            errors.append(error)
    workers = [Thread(target=call, args=(role,)) for role in ("OPTIONS", "FUTURES")]
    try:
        for worker in workers:
            worker.start()
        assert entered.wait(1)
        # Wait for both reservations, without market/model network calls.
        from time import monotonic, sleep
        deadline = monotonic()+1
        while client.requests != 2 and monotonic() < deadline:
            sleep(.001)
        assert client.requests == 2
        with pytest.raises(GrokUnavailable, match="BUSY"):
            client.request("MASTER", "", {"mode": "PAPER"}, {})
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert not errors


@pytest.mark.parametrize("answer", [{"proposals": [{}]}, {"proposals": [
    {"symbol": "BTC/USD", "action": "BUY", "reason": "x"}]},
    {"proposals": [{"symbol": "BTC/USD", "action": "HOLD", "reason": "x", "shell": "x"}]},
    {"proposals": [], "code": "x"}])
def test_crypto_schema_rejects_missing_or_extra_authority(answer):
    provider = GrokProposalProvider(GrokClient("key", "grok-test", transport=lambda _: response(answer)))
    with pytest.raises(GrokUnavailable, match="INVALID_PROPOSALS"):
        provider.propose({"mode": "PAPER", "asset": "CRYPTO"})


def test_only_grok_configuration_is_used(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_GROK_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "old-key")
    monkeypatch.setenv("ATLAS_SUPERVISOR_MODEL", "old-model")
    assert configured_provider() is None
    monkeypatch.setenv("XAI_API_KEY", "local-test-key")
    monkeypatch.setenv("ATLAS_GROK_MODEL", "grok-test")
    assert isinstance(configured_provider(), GrokProposalProvider)
    monkeypatch.setenv("ATLAS_GROK_MODEL", "https://evil.example")
    with pytest.raises(ValueError):
        configured_client()
    assert configured_suite() is None  # desktop/protection survives bad AI config


def candidate(engine=EngineId.OPTIONS, **changes):
    values = dict(engine=engine, candidate_id=engine.value+"|candidate",
                  valid_until=NOW+timedelta(seconds=20), evidence_json='{"net_reward":"10"}')
    values.update(changes)
    return Candidate(**values)


def client_for(answer_change=None, clock=lambda: 10):
    def transport(request):
        context = json.loads(json.loads(request.data)["input"][1]["content"])
        answer = {"request_id": context["request_id"],
                  "selected_ids": [context["candidates"][0]["candidate_id"]], "reason": "Evidence"}
        if answer_change:
            answer.update(answer_change)
        return response(answer)
    return GrokClient("key", "grok-test", transport=transport, clock=clock)


def test_engine_to_master_uses_original_candidate_without_mutation():
    suite = GrokSuite(client_for(), clock=lambda: NOW)
    c = candidate()
    advice = suite.review(EngineId.OPTIONS, (c,), evidence={"history": "completed"})
    master = suite.coordinate(account_evidence={"capital_available": "2500"})
    assert master.role == "MASTER"
    assert advice.session == master.session
    assert advice.selected == master.selected == (c,)
    assert master.selected[0] is c


@pytest.mark.parametrize("changes", [{"request_id": "old"}, {"selected_ids": ["unknown"]},
    {"selected_ids": ["OPTIONS|candidate", "OPTIONS|candidate"]}, {"selected_ids": [1]}])
def test_wrong_or_replayed_selection_is_rejected(changes):
    adviser = GrokAdviser(client_for(changes), "OPTIONS", "session", "policy", clock=lambda: NOW)
    with pytest.raises(GrokUnavailable):
        adviser.review((candidate(),), evidence={})


def test_cross_engine_and_expired_candidate_never_calls_model():
    adviser = GrokAdviser(client_for(), "OPTIONS", "session", "policy", clock=lambda: NOW)
    for c in (candidate(EngineId.FUTURES), candidate(valid_until=NOW)):
        with pytest.raises(ValueError):
            adviser.review((c,), evidence={})
    assert adviser.client.requests == 0


def test_expiry_during_slow_response_discards_selection():
    clock = iter((NOW, NOW+timedelta(seconds=25)))
    adviser = GrokAdviser(client_for(), "OPTIONS", "session", "policy", clock=lambda: next(clock))
    with pytest.raises(GrokUnavailable, match="EXPIRED"):
        adviser.review((candidate(),), evidence={})


def test_master_ignores_expired_engine_mail():
    now = [NOW]
    suite = GrokSuite(client_for(), clock=lambda: now[0])
    suite.review(EngineId.OPTIONS, (candidate(),), evidence={})
    now[0] += timedelta(seconds=25)
    master = suite.coordinate(account_evidence={})
    assert master.selected == () and master.reason == "NO_CANDIDATES"
    assert suite.client.requests == 1


def test_failed_new_review_revokes_old_recommendation():
    suite = GrokSuite(client_for(), clock=lambda: NOW)
    suite.review(EngineId.OPTIONS, (candidate(),), evidence={})
    with pytest.raises(GrokUnavailable, match="COOLDOWN"):
        suite.review(EngineId.OPTIONS, (candidate(),), evidence={})
    assert suite.coordinate(account_evidence={}).selected == ()


@pytest.mark.parametrize("intent", list(IntentKind)[1:])
def test_management_intents_keep_owner_and_revision(intent):
    c = candidate(intent=intent, lifecycle_id="OPTIONS|position", order_id="order-1", expected_revision=7)
    suite = GrokSuite(client_for(), clock=lambda: NOW)
    suite.review(EngineId.OPTIONS, (c,), evidence={})
    selected = suite.coordinate(account_evidence={}).selected[0]
    assert selected.intent == intent and selected.expected_revision == 7
    assert selected.lifecycle_id == "OPTIONS|position"


@pytest.mark.parametrize("changes", [{"lifecycle_id": "FUTURES|position"},
    {"expected_revision": None}, {"expected_revision": True}, {"order_id": None}])
def test_management_without_authoritative_owner_revision_or_order_is_rejected(changes):
    values = dict(intent=IntentKind.REPLACE_ENTRY, lifecycle_id="OPTIONS|position",
                  order_id="order-1", expected_revision=7)
    values.update(changes)
    with pytest.raises(ValueError):
        candidate(**values)
