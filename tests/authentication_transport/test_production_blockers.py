from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/authentication_transport").glob("*.py"))
    prohibited = (
        "webull", "oauth", "jwt", "cookie", "captcha", "mfa", "os.environ",
        "getenv(", "dotenv", "keyring", "retry", "backoff", "sleep(", "threading",
        "asyncio", "datetime.now", "utcnow", "uuid", "random", "hashlib",
        "account", "order", "market data", "signing", "sqlite", "open(",
    )
    assert not [term for term in prohibited if term in source]


def test_executable_transport_tests_use_mock_transport_and_reserved_host_only():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("tests/authentication_transport").glob("*.py"))
    assert "mocktransport" in source
    assert "https://mock.invalid" in source
    assert "https://" + "example.com" not in source
    assert "https://" + "httpbin" not in source
