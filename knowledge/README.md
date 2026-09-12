# Atlas historical research data

This is a research-only pipeline. It has no trading or broker-order code.

1. Create a free Alpaca account and place `ALPACA_API_KEY` and
   `ALPACA_API_SECRET` in the local environment (or the repository's ignored
   `.env` convention).
2. Check access with:

```powershell
python -m app.trade_intelligence.knowledge provider-check --provider alpaca --feed iex
```

3. Review a plan without network requests:

```powershell
python -m app.trade_intelligence.knowledge dry-run --provider alpaca --feed iex --start 2024-01-01 --end 2024-01-31
```

4. A small local normalized JSONL fixture can be mined end-to-end with:

```powershell
python -m app.trade_intelligence.knowledge run --provider alpaca --feed iex --start 2024-01-01 --end 2024-01-02 --input data\research\sample-bars.jsonl
```

The same `run` command is resumable for local normalized inputs. The default
without `--input` is a dry plan; bulk acquisition is deliberately not started
automatically during this reviewed phase. Raw and normalized acquisition
partitions belong under `data/research/market_data/`; the existing knowledge
corpus belongs under `data/research/trading_knowledge/`. These paths are
ignored by Git.

```powershell
python -m app.trade_intelligence.knowledge status
python -m app.trade_intelligence.knowledge validate
python -m app.trade_intelligence.knowledge report
```

For the first research tranche, use the neutral cohort preset (it reports
sample-size and concentration warnings and never changes trading policy):

```powershell
python -m app.trade_intelligence.knowledge report --preset first-tranche
python -m app.trade_intelligence.knowledge report --group-by strategy,time_of_day_bucket
python -m app.trade_intelligence.knowledge report --group-by strategy_combination --split train
```

Feature snapshots are persisted at acceptance with schema and derivation
versions. Older episodes without snapshots remain explicitly feature-
unavailable; enrichment requires writing a new corpus output. Canonical
TRAIN/VALIDATION/TEST splits are chronological, and all metrics are
observational research only.

The source policy is `ALPACA / IEX`, `ALPACA_IEX_FREE`, raw adjustment, and
`SINGLE_EXCHANGE_FREE_RESEARCH`. No paid SIP fallback is attempted.
