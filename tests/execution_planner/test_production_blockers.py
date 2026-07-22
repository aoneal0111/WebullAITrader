from pathlib import Path
def test_no_order_runtime_broker_auth_session_transport_or_nondeterminism():
 text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/execution_planner").glob("*.py"));prohibited=("orderplacementruntime","deterministicorderplacementruntime","broker_gateway","app.broker","webull","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","poll","sleep(","threading","asyncio","while ")
 assert not [x for x in prohibited if x in text]
