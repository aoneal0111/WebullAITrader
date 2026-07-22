from pathlib import Path
def test_no_prohibited_capabilities():
 text="\n".join(p.read_text(encoding="utf-8") for p in Path("app/positions").glob("*.py")).lower();prohibited=("os.environ","getenv(","open(","pathlib","requests","httpx","socket","datetime.now","utcnow","uuid","random","retry","threading","asyncio","sleep(","webull","submit_order","market.data","account_information")
 assert not [term for term in prohibited if term in text]
