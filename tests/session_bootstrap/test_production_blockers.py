from pathlib import Path
def test_no_prohibited_capabilities():
 source="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/session_bootstrap").glob("*.py"));prohibited=("os.environ","getenv(","dotenv","pathlib","read_text","open(","requests","httpx","socket","sqlite","database","retry","backoff","sleep(","threading","asyncio","datetime.now","utcnow","uuid","random","hashlib","login(","refresh(","activate(","place_order")
 assert not [x for x in prohibited if x in source]
