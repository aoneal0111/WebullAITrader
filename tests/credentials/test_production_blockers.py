from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/credentials").glob("*.py"))
    prohibited = (
        "os.environ", "dotenv", "keyring", "win32cred", "secretstorage", "vault",
        "import requests", "import httpx", "import socket", "import urllib", "threading",
        "sleep(", "datetime.now", "utcnow", "uuid", "random", "hashlib", "oauth",
        "token refresh", "windows credential manager", "keychain", "user prompt",
    )
    assert not [term for term in prohibited if term in source]


def test_provider_exposes_no_authentication_storage_or_session_api():
    from app.credentials import ValidatingCredentialProvider
    forbidden = ("authenticate", "login", "logout", "cache", "refresh", "save", "load")
    assert not [name for name in forbidden if hasattr(ValidatingCredentialProvider, name)]
