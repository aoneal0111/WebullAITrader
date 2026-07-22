from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    source = "\n".join(path.read_text(encoding="utf-8").lower()
                       for path in Path("app/http_pipeline").glob("*.py"))
    prohibited = (
        "import httpx", "import requests", "import socket", "import urllib", "dns",
        "authentication", "cookie", "session", "threading", "sleep(",
        "datetime.now", "utcnow", "uuid", "random", "webull", "redirect", "compression",
    )
    assert not [term for term in prohibited if term in source]


def test_pipeline_exposes_no_transport_operation():
    from app.http_pipeline import DeterministicHTTPRequestPipeline
    forbidden = ("send", "execute", "request", "retry", "connect")
    assert not [name for name in forbidden if hasattr(DeterministicHTTPRequestPipeline, name)]
