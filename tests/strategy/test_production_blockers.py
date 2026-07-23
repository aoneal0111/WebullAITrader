from pathlib import Path
def test_no_broker_execution_authentication_session_or_transport_capabilities():
 files=("models.py","policies.py","interfaces.py","validation.py","runtime.py","serializers.py","exceptions.py");text="\n".join(Path("app/strategy",name).read_text(encoding="utf-8").lower() for name in files);prohibited=("app.broker","webull","place_order","cancel_order","submit_order","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","sleep(","threading","asyncio","while ")
 assert not [term for term in prohibited if term in text]
