# Five additional crypto candidates with published win rates above 60%

Reviewed 2026-11-06. These are sourced reproduction candidates, not five verified
Atlas/Kraken winners. No strategy, process, trading setting, wallet or model was
activated or changed. Exact source commits, evidence files, hashes and known
binding gaps are in `crypto-next-five-source-lock.json`.

| Candidate | Published win rate | Trades | Evidence scope |
|---|---:|---:|---|
| E0V1E | 79.8% | 2,481 | Author repository README, 2022-06 to 2023-06; matching parameter file missing in inspected checkout. |
| NASOS HOv3 | 96.4% | 991 | Freqle full-performance section, 2022-2024, 0.1% assumed fee per side; repaired v3 runtime, not byte-bound to upstream v2 yet. |
| BB_RPB_TSL v104 | 90.9% | 1,866 | Committed 2021 report; includes six forced closures, 97 possible positions, and large losses. |
| Connors RSI baseline | 69.3% | 3,543 | README BTC/ETH combined result; long/short, costs not found in inspected baseline. |
| WaveRider SOL long | 72.5% | 51 | Published per-pair WFO result; private validation pipeline prevents independent reproduction of that claim. |

NASOS's full-performance result is distinct from the page's separate league
headline; do not mix its trade count, dates, return or win rate with the league
result. CRSI's baseline is also distinct from its optimized BB-slope variant.
WaveRider's SOL long percentage is not an all-coin or whole-agent percentage.

E0V1E and NASOS are first in the compatibility/reproduction queue because they
already target Freqtrade spot. That ordering is not performance approval.
BB_RPB requires resolving v104 source binding and may add NFI-correlated entries.
CRSI needs a separately identified long-only spot adaptation with costs.
WaveRider remains an exploratory source hypothesis while its original WFO
evidence is unavailable. None can yet inherit a published percentage as an
Atlas forward expectation.

Sources:

- E0V1E: https://github.com/TheoBrigitte/freqtrade/tree/c9def379b1036064d592c3a0518a07811f5a9a76/strategies/e0v1e
- NASOS result: https://freqle.org/strategy/d8c6b3f47d4be717
- NASOS upstream: https://github.com/neozhou2009/freqtrade-strategies/blob/fcca4013e724aacca72cd0599259466c487a1d04/strategies/NotAnotherSMAOffsetStrategyHOv3/NotAnotherSMAOffsetStrategyHOv3.py
- BB_RPB report: https://github.com/jilv220/BB_RPB_TSL/blob/33f83596e23ca214ef12352802aa798cb986b236/backtest_result_v104/20210507-0922.txt
- CRSI: https://github.com/MRCVEngineer/CRSI/tree/d826a8e4d55c0d7a993e6f06303fcdb34205d31e
- WaveRider WFO: https://github.com/JudyaiLab/hackathon-trading-agent/blob/b2f92dd9cd8af334f918640fe358424f031d1fb3/docs/wfo_summary.md
