from pathlib import Path
def test_no_io_execution_or_persistence_capabilities():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/webull_protocol_evidence").glob("*.py"));prohibited=("mock.invalid","os.environ","getenv(","dotenv","pathlib","read_text","open(","requests","httpx","socket","selenium","playwright","subprocess","sqlite","database","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","authenticate(","create_session","place_order")
 assert not [x for x in prohibited if x in source]
def test_fixtures_are_synthetic_reserved_and_contain_no_complete_payloads():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("tests/webull_protocol_evidence").glob("*.py"));assert "synthetic" in source;assert "https://mock.invalid/authenticate" in source;assert "production"+".webull" not in source;assert '"request'+'_body"' not in source
