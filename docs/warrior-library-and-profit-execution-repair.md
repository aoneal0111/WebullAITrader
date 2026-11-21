# Warrior library and profit execution repair

## What changed since the original Warrior

This review concerns Warrior Momentum. Quick Scalper is a separate, newer strategy. The November 13 closed review contains Scalper fills and no Warrior filled position; its losing trades do not establish how the older Warrior would have performed.

Source history establishes these changes:

- August 10 (`3a6c976`): native momentum patterns, first target 50% at 1R, second 25% at 2R, runner 25%, break-even after 1R. No imported taxonomy execution path existed in that initial version.
- October 7 (`6aaad59`): executable-bid profit harvest and peak-profit locking existed alongside later management improvements. The current policy harvests 25% at 0.75R, another 25% at 2R, and limits the runner at 4R. Retention thresholds are not changed here.
- October 30 (`b164c15`): the early-Warrior/cent-Scalper PAPER experiment restricted native Warrior detection and permission to MICRO_PULLBACK, MOMENTUM_REACCELERATION and RECLAIM_CONTINUATION. Subsequent account-sized and continuous modes inherited this restriction.
- November 3 (`8c41e71`): mandatory Grok native review plus a separate Director plan path. That approval layer remains in place in this repair.
- At the reviewed runtime (`55bebeacd`), taxonomy detection is explicitly advisory; `taxonomy_signal` cannot replace the canonical Warrior result. The async path discards that signal. Family attribution in cent-derived profiles also ignores the original taxonomy name. Thus importing a library was not equivalent to letting its families trade.

The repository has **30 taxonomy entries: 23 implemented active detectors and seven unavailable/research entries**, not 26 working trading systems. There is no verified August performance ledger in this package, so these are source changes, not a claim that an August version was more profitable.

## Explicit continuous PAPER migration

`warrior_library_repair` validates an existing continuous operation, saves an exact copy of its previous spec, and sets `IMPLEMENTED_TAXONOMY_PAPER_V1`. It preserves the operation ID, existing ledgers, entry sessions, budgets and deadlines. No process, provider request or order is started.

The selected policy removes the inherited three-pattern restriction for Warrior. All 23 implemented detectors are evaluated against bounded completed, same-day bars. Their triggered geometry can enter before Warrior's normal technical, quote, opportunity, account, reward, risk, Grok review and ownership checks. Existing valid native signals retain priority. One overlapping structural episode retains one execution identity; it does not become 23 orders.

Setup completion uses the existing native 90/65 triggered/forming scale; it is not a probability estimate. A proposal does not consume execution identity before downstream authorization. Current detections, rather than historical merged memberships, supply candidate geometry. Invalidated or unavailable patterns cannot authorize a trade. No timestamps, bar prices or fills are invented. The selector performed about 3.7ms per evaluation on a small offline fixture; this is not a loaded Windows latency guarantee.

Original taxonomy IDs survive into order permission and Grok native-review evidence. Director requests receive the complete catalogue with implementation/unavailable status. Status output lists the selected policy, permitted families and catalogue. Source fingerprints include detector, context, adapter and bridge code.

Seven entries remain unavailable: SECOND_PULLBACK_CONTINUATION, PRIOR_RESISTANCE_BREAKOUT, VWAP_RECLAIM, VWAP_PULLBACK_HOLD, RED_TO_GREEN_MOMENTUM, HALT_RESUMPTION_CONTINUATION, PARABOLIC_CONTINUATION. They need defined implementations or authoritative inputs. None is relabeled as a working strategy. Session-specific patterns still require their session evidence; an opening-range breakout is not invented after hours.

## Profit-taking execution correction

Warrior's PROFIT_HARVEST_1/2/3 are decisions to realize an already observed executable gain. Previously they became resting LIMIT sells at the observed bid. A lower subsequent bid could leave that partial sale unfilled. The repair submits these three roles as MARKET partial sells. Planned FIRST_TARGET, SECOND_TARGET and runner targets retain their LIMIT behavior.

Market harvest is separate from full liquidation: a correlated stop continues to cover the runner and unfilled harvest. Role/quantity ownership, failed-submission retry and restart recovery remain canonical. A harvest milestone now advances only after its owned order is completely filled; a partial fill retains its pending role and cannot prematurely schedule the next harvest. A market order is not a guaranteed profitable fill and can slip or gap. Tests use the next actual synthetic quote, including a retreat, rather than crediting the earlier peak. Stop and target thresholds, partial fractions and entry thresholds are unchanged.

## Verification and limits

Validation: 415 focused checks passed on Python 3.13.5/Linux. The Windows installer repeats the selected checks locally before updating. Focused checks cover each of the 23 family contracts, original-name admission, rejected proposal retry, invalidation, real detector-to-native signal routing, no live/automatic execution, unchanged Scalper configuration, exact spec backup, partial harvest on a declining next bid, and partial-fill/stop/restart races. The older advisory mode remains the default unless explicitly migrated. Legacy PAPER and LIVE operation are not silently migrated. Crypto code, wallets and processes are untouched.

These fixes address verified architectural disconnections and an exit order-type mismatch. They do not establish a future win rate, daily profit, the exact causes of every historical missed entry, or external Webull reliability. The installed machine still needs a PAPER observation after migration; this repository cannot inspect its running feed or broker session directly.
