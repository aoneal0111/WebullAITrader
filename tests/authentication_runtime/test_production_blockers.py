from pathlib import Path
def test_prohibited_capabilities_absent():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/authentication_runtime").glob("*.py"));prohibited=("mock.invalid","oauth","jwt","cookie","os.environ","dotenv","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","account","order","market data","httpx","webull")
 assert not [x for x in prohibited if x in source]
def test_transport_execution_is_mock_only():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("tests/authentication_runtime").glob("*.py"));assert "mocktransport" in source;assert "https"+"://" not in source
