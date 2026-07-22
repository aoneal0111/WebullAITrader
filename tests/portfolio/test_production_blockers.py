from pathlib import Path
def test_no_prohibited_dependencies_or_capabilities():
 text="\n".join(p.read_text(encoding="utf-8") for p in Path("app/portfolio").glob("*.py")).lower();prohibited=("market_data","broker_gateway","authentication","app.session","httpx","requests","socket","os.environ","getenv(","open(","pathlib","datetime.now","utcnow","uuid","random","retry","sleep(","threading","asyncio","while ")
 assert not [term for term in prohibited if term in text]
