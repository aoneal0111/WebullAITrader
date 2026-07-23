from pathlib import Path
def test_no_prohibited_capabilities():
 files=("__init__.py","runtime.py","interfaces.py","models.py","policies.py","validation.py","serializers.py","exceptions.py")
 text="\n".join((Path("app/market_data")/name).read_text(encoding="utf-8") for name in files).lower();prohibited=("os.environ","getenv(","open(","pathlib","requests","httpx","socket","datetime.now","utcnow","uuid","random","retry","threading","asyncio","sleep(","webull","submit_order","account_information","app.positions","authentication")
 assert not [term for term in prohibited if term in text]
