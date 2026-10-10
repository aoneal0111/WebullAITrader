# Contract engine logic: initial implementation

`app.asset_modules.contract_logic` provides pure PAPER planning functions,
not a broker adapter or an activated desktop engine.

- Options: SPY/QQQ only; a bullish underlying signal selects long calls,
  bearish selects long puts. Premium targets are explicitly 0.05 or 0.10.
  Quotes must belong to the actual option contract. Integer sizing includes
  multiplier, spread, declared fees, risk and capital budgets. Candidates are
  alternatives: the controller must select/reserve one, not buy the entire list.
- Futures: supports long and short plans with supplied contract multiplier,
  tick and margin. No equity-share sizing, inferred margin or synthetic contract
  specifications. Breakout direction requires completed contiguous bars and
  8/21 EMA agreement with a break of the prior 20-bar range.
- Stops and targets use executable bid for longs and ask for shorts. A fresh
  quote is required even for a time exit; missing quotes return no exit signal,
  not a fictional fill. Protection orchestration must surface that absence.

Defaults are experimental mechanics, not selected winners. No external code
was copied and no external trading result was reproduced.

Remaining integration: Webull contract discovery and quote/history adapters,
session calendar/expiry/roll handling, controller admission and durable paper
fills/positions, engine worker composition, GUI projection and model proposals.
Until these exist, Options/Futures readiness remains planning-only. Crypto's
existing supervisor and Warrior/Scalper runtime have not changed.

## AI and repository review, 2026-10-10

- [TradingAgents](https://github.com/TauricResearch/TradingAgents): analyst,
  researcher, trader, risk and portfolio-manager roles; Claude/Grok supported.
  Separate fast/deep model tiers are useful architecture references. Its README
  explicitly says published returns need not reproduce and some historical
  runs still receive current news/social data. Avoid using those runs as clean
  historical evidence for Atlas.
- [HKUDS AI-Trader](https://github.com/HKUDS/AI-Trader): current agent-native
  service and separate background workers; useful for isolation and experiment
  tracking. Supporting Claude Code is not proof Claude executes profitable
  low-latency option trades.
- [AI-Trader paper](https://arxiv.org/html/2512.10971v1): public cross-market
  model comparison. Its model/date/market results cannot be transferred to
  SPY/QQQ premium scalping without matching execution and costs.
- [HKU six-week live study](https://www.hkubs.hku.hk/media/school-news/testing-ai-in-the-real-world-hku-business-school-released-ai-agents-trading-performance/):
  common capital/tools/data; Claude incurred losses, and more trades did not
  establish better returns. This is foreign-exchange-oriented evidence, not
  a head-to-head Atlas equity/options test.
- [LEAN options universes](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/equity-options),
  [Freqtrade exits](https://docs.freqtrade.io/en/latest/strategy-callbacks/),
  [pysystemtrade](https://github.com/pst-group/pysystemtrade): instrument-aware
  selection, explicit exit policies and trend methods are useful references.
  We are not importing their portfolio assumptions or headline performance.

Initial research finding: no model was established as the best trader by these
sources. The operator subsequently selected Grok for all Atlas AI roles;
[the Grok coordination implementation](grok-engine-coordination.md) follows that
architecture decision, without claiming a proven trading advantage. Models rank/explain proposals; a
deterministic controller retains reservations and order ownership. Stops must
operate independently of a slow or unavailable model.

Verification: the combined contract/Grok/supervisor/desktop focused run passed
51 tests on supported Python 3.13.16 with project dependencies installed. The
broader asset-module and desktop-layout run passed 229 tests. No market connection
or authenticated AI request was made; profitability and Windows runtime behavior
remain unverified by these mechanics checks.
