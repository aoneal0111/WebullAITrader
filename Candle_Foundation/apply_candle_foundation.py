from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path.cwd()
PAYLOAD = Path(__file__).resolve().parent
MARKET_DATA = ROOT / "app" / "market_data"


def fail(message: str) -> None:
    raise SystemExit(message)


def copy_file(relative: str) -> None:
    source = PAYLOAD / relative
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"ADD/UPDATE {relative}")


def update_market_data_init() -> None:
    path = MARKET_DATA / "__init__.py"
    text = path.read_text(encoding="utf-8")
    original = text

    import_anchor = "from app.market_data.models import *\n"
    imports = (
        "from app.market_data.candle_models import Candle,CandleSeries,TimeFrame\n"
        "from app.market_data.candle_aggregator import CandleAggregator\n"
        "from app.market_data.interval_bucket import bucket_end,bucket_start\n"
    )
    if imports not in text:
        if import_anchor not in text:
            fail("Could not find market_data import anchor; no files were changed.")
        text = text.replace(import_anchor, import_anchor + imports, 1)

    export_anchor = ' "recorded_session","replay_all","resume","seek",\n)'
    replacement = (
        ' "recorded_session","replay_all","resume","seek",\n'
        ' "Candle","CandleSeries","TimeFrame","CandleAggregator","bucket_start","bucket_end",\n)'
    )
    if '"CandleAggregator"' not in text:
        if export_anchor not in text:
            fail("Could not find market_data __all__ anchor; no files were changed.")
        text = text.replace(export_anchor, replacement, 1)

    if text != original:
        backup = path.with_suffix(f".py.candle-foundation-{datetime.now():%Y%m%d-%H%M%S}.bak")
        shutil.copy2(path, backup)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"UPDATE app/market_data/__init__.py (backup: {backup.name})")
    else:
        print("UNCHANGED app/market_data/__init__.py")


def main() -> None:
    if not (MARKET_DATA / "models.py").is_file():
        fail("Run this installer from the WebullAITrader repository root.")

    update_market_data_init()
    for relative in (
        "app/market_data/candle_models.py",
        "app/market_data/interval_bucket.py",
        "app/market_data/candle_aggregator.py",
        "tests/market_data/test_candle_models.py",
        "tests/market_data/test_interval_bucket.py",
        "tests/market_data/test_candle_aggregator.py",
    ):
        copy_file(relative)

    print("\nADD SUMMARY")
    print("  app/market_data/candle_models.py")
    print("  app/market_data/interval_bucket.py")
    print("  app/market_data/candle_aggregator.py")
    print("  tests/market_data/test_candle_models.py")
    print("  tests/market_data/test_interval_bucket.py")
    print("  tests/market_data/test_candle_aggregator.py")
    print("UPDATE SUMMARY")
    print("  app/market_data/__init__.py")
    print("DELETE SUMMARY")
    print("  None")


if __name__ == "__main__":
    main()
