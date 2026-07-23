from pathlib import Path


def test_package_has_no_prohibited_capabilities():
    root=Path("app/account_information")
    text="\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py")).lower()
    prohibited=("os.environ","getenv(","open(","pathlib","requests","httpx","socket","datetime.now","utcnow","uuid","random","retry","threading","asyncio","sleep(","webull","market data","submit_order")
    assert not [term for term in prohibited if term in text]
