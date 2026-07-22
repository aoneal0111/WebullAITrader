from pathlib import Path


def test_paper_trading_has_no_forbidden_dependencies_or_hidden_state():
    milestone_files = ("exceptions.py", "interfaces.py", "milestone_models.py", "policies.py", "runtime.py", "serializers.py", "validation.py")
    text = "\n".join((Path("app/paper_trading") / name).read_text(encoding="utf-8").lower() for name in milestone_files)
    prohibited = ("webull", "orderplacementruntime", "broker_gateway", "authentication", "app.session", "httpx",
                  "requests", "socket", "os.environ", "getenv(", "datetime.now", "utcnow", "uuid", "random",
                  "retry", "poll", "sleep(", "threading", "asyncio", "open(", "pathlib", "sqlite", "database")
    assert not [item for item in prohibited if item in text]


def test_no_module_level_account_registry():
    text = Path("app/paper_trading/runtime.py").read_text(encoding="utf-8")
    assert "self._account" not in text and "ACCOUNTS" not in text and "registry" not in text.lower()
