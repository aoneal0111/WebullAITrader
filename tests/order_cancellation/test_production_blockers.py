from pathlib import Path
def test_no_prohibited_capabilities_or_operations():
 text="\n".join(p.read_text(encoding="utf-8") for p in Path("app/order_cancellation").glob("*.py")).lower();prohibited=("os.environ","getenv(","open(","pathlib","requests","httpx","socket","datetime.now","utcnow","uuid","random","retry","threading","asyncio","sleep(","webull","place_order","get_order_status","get_open_orders","market_data","account_information","app.positions","while ")
 assert not [term for term in prohibited if term in text]
