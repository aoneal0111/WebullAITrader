from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/authentication").glob("*.py"))
    prohibited = (
        "import requests", "import httpx", "import socket", "oauth", "jwt", "cookie",
        "os.environ", "getenv(", "dotenv", "keyring", "threading", "sleep(",
        "datetime.now", "utcnow", "uuid", "random", "hashlib", "webull",
    )
    assert not [term for term in prohibited if term in source]


def test_service_has_no_network_session_or_persistence_operations():
    from app.authentication import DeterministicAuthenticationService
    forbidden = ("send", "request", "refresh", "save", "load", "start", "schedule")
    assert not [name for name in forbidden if hasattr(DeterministicAuthenticationService, name)]
