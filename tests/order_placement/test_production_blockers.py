from pathlib import Path
def test_no_prohibited_capabilities():
 text="\n".join(p.read_text(encoding="utf-8") for p in Path("app/order_placement").glob("*.py")).lower();prohibited=("os.environ","getenv(","open(","pathlib","requests","httpx","socket","datetime.now","utcnow","uuid","random","retry","threading","asyncio","sleep(","webull","market_data","account_information","app.positions","cancel_order","get_order_status")
 assert not [term for term in prohibited if term in text]
