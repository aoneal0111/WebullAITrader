from pathlib import Path


def test_package_is_broker_neutral_and_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/httpx_transport").glob("*.py"))
    prohibited = (
        "webull", "oauth", "jwt", "cookie", "os.environ", "getenv(", "dotenv",
        "keyring", "retry", "backoff", "sleep(", "threading", "asyncio",
        "datetime.now", "utcnow", "uuid", "random", "hashlib", "authentication",
        "credentials", "session", "sqlite", "open(",
    )
    assert not [term for term in prohibited if term in source]


def test_tests_use_only_mock_or_structural_clients_and_reserved_hostnames():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("tests/httpx_transport").glob("*.py"))
    assert "mocktransport" in source
    assert "https://mock.invalid" in source
    assert "https://" + "httpbin" not in source
    assert "https://" + "example.com" not in source
