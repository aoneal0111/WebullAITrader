from pathlib import Path


def test_no_live_or_nondeterministic_dependencies():
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in Path("app/execution_orchestrator").glob("*.py"))
    prohibited = ("orderplacementruntime", "deterministicorderplacementruntime", "webull", "broker_gateway", "authentication",
                  "app.session", "httpx", "requests", "socket", "os.environ", "getenv(", "datetime.now", "utcnow", "uuid",
                  "random", "retry", "poll", "sleep(", "threading", "asyncio", "open(", "pathlib", "sqlite", "database")
    assert not [item for item in prohibited if item in text]


def test_runtime_has_no_hidden_account_or_stage_state():
    text = Path("app/execution_orchestrator/runtime.py").read_text(encoding="utf-8")
    assert "self._account" not in text and "self._last_result" not in text and "registry" not in text.lower()
