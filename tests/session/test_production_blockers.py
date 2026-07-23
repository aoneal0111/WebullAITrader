from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/session").glob("*.py"))
    prohibited = (
        "import requests", "import httpx", "import socket", "oauth", "jwt", "cookie",
        "os.environ", "getenv(", "dotenv", "keyring", "threading", "sleep(",
        "datetime.now", "utcnow", "uuid", "random", "hashlib", "webull", "timer",
    )
    assert not [term for term in prohibited if term in source]


def test_manager_has_no_transport_scheduling_or_persistence_api():
    from app.session import DeterministicSessionManager
    forbidden = ("send", "schedule", "start", "save", "load", "refresh")
    assert not [name for name in forbidden if hasattr(DeterministicSessionManager, name)]
