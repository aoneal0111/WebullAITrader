from pathlib import Path
def test_runtime_has_no_broker_order_auth_session_transport_or_nondeterminism():
 files=("runtime.py","interfaces.py","validation.py","serializers.py","exceptions.py");text="\n".join(Path("app/risk",n).read_text(encoding="utf-8").lower() for n in files);prohibited=("app.broker","webull","place_order","submit_order","cancel_order","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","poll","sleep(","threading","asyncio","while ")
 assert not [x for x in prohibited if x in text]
