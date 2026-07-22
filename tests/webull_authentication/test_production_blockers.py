from pathlib import Path
def test_prohibited_capabilities_absent():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/webull_authentication").glob("*.py"));prohibited=("oauth","jwt","cookie","selenium","captcha","mfa","token refresh","os.environ","dotenv","keyring","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","account","order","market data","requests","httpx")
 assert not [x for x in prohibited if x in source]
def test_fixtures_are_synthetic_and_only_reserved_target():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("tests/webull_authentication").glob("*.py"));assert "synthetic-profile" in source;assert "https://mock.invalid/authenticate" in source;assert "actual-password-value" not in source.replace('"actual-password-value"','')
