from pathlib import Path
def test_no_prohibited_capabilities_or_protocol_defaults():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/webull_authentication_config").glob("*.py"));prohibited=("mock.invalid","oauth","jwt","cookie","os.environ","getenv(","dotenv","pathlib","open(","read_text","keyring","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","account","order","market data","httpx")
 assert not [x for x in prohibited if x in source]
def test_fixtures_explicitly_synthetic_and_mock_only():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("tests/webull_authentication_config").glob("*.py"));assert "synthetic-config" in source;assert "https://mock.invalid/authenticate" in source;assert "mocktransport" in source
