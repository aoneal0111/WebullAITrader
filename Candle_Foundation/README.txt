Milestone 6.1-6.3: Canonical Candle Foundation

Run from the WebullAITrader repository root:

  Expand-Archive .\WebullAITrader_Candle_Foundation.zip -DestinationPath .\Candle_Foundation
  .\Candle_Foundation\install_candle_foundation.ps1

The installer requires a clean Git working tree and runs:

  python -m compileall app
  python -m pytest
  git diff --check

Added:
- app/market_data/candle_models.py
- app/market_data/interval_bucket.py
- app/market_data/candle_aggregator.py
- tests/market_data/test_candle_models.py
- tests/market_data/test_interval_bucket.py
- tests/market_data/test_candle_aggregator.py

Updated:
- app/market_data/__init__.py

Deleted:
- Nothing
