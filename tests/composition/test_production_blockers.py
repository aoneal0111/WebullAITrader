from pathlib import Path


def test_composition_has_no_prohibited_capabilities():
    root = Path("app/composition")
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in root.glob("*.py"))
    prohibited = (
        "import requests", "import httpx", "import socket", "import urllib",
        "authentication", "credential", "os.environ", "getenv(", "configparser",
        "yaml", "toml", "threading", "sleep(", "datetime.now", "utcnow",
        "uuid", "random",
    )
    assert not [term for term in prohibited if term in source]


def test_composition_exposes_no_execution_or_configuration_loading_api():
    from app.composition import CompositionRoot
    forbidden = ("execute", "authenticate", "login", "load_config", "read_environment")
    assert not [name for name in forbidden if hasattr(CompositionRoot, name)]
