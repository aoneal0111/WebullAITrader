from pathlib import Path
def test_package_has_no_execution_io_or_activation_capability():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/webull_authentication_approval").glob("*.py"));prohibited=("mock.invalid","os.environ","getenv(","dotenv","pathlib","read_text","open(","requests","httpx","socket","selenium","playwright","subprocess","sqlite","database","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","authenticate(","activate(","create_session","place_order")
 assert not [x for x in prohibited if x in source]
def test_fixtures_synthetic_no_network_or_real_secrets():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("tests/webull_authentication_approval").glob("*.py"));assert "synthetic" in source;assert "mock"+"transport" not in source;assert "real-password-value" not in source.replace('"real-password-value"','');assert "production"+".webull" not in source
