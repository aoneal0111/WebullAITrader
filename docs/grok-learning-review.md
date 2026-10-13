# Grok learning review: durable research feedback

Atlas now has a distinct `LEARNING` Grok role alongside engine advisers and the
master. It uses the existing bounded transport, request budget and credential
handling. No additional API client, key, network request or desktop worker starts
automatically. No runtime engine is subscribed in this stage.

`LearningJournal` imports raw `ENGINE_COMPARISON_V1` experiments, validates them
through the existing paired-cohort comparison, and retains their original data
in a dedicated SQLite journal. One frozen experiment ID cannot silently change
its data or costs. Exact reimports are idempotent. Use a new ID for a new experiment.

Feedback covers Warrior, Scalper, Crypto, Options and Futures independently.
Comparisons keep declared dataset, cost model, evidence kind and policy versions.
Missing/unresolved episodes cannot contribute invented P&L. The reviewer sees
win rate, net expectancy, profit factor, paired sample size, exclusions and
day/symbol concentration. Prior five review identities/selections supply memory,
explicitly separate from the new experiment's trade samples. They are not pooled
or counted as additional evidence. Overlapping experiments are not deduplicated
across reports; the reviewer must not infer independent sample growth.

Only positive-net challengers beating the declared champion on the same paired
episodes are eligible for nomination. This is a narrow trial-screening rule,
not a proof of superiority. A high win rate alone cannot qualify a losing policy.
Small samples can be nominated for more evidence, but cannot activate anything.
The model may hold, or select at most one supplied candidate per engine.

Each experiment has at most one attempted model review. Selection, hold or
failure survives restart. An interrupted in-flight request remains `REQUESTED`
and is not retried automatically. Unknown IDs, duplicates, additional authority
fields and invalid responses produce no selection.

`GrokLearningLoop` supplies an explicitly started, interruptible 60-second
review loop. Each tick reviews one newly imported experiment, oldest first,
then idles when none remain. Construction starts no worker. The caller supplies
the shared client, stop event and output callback. This loop does not collect
trade outcomes itself and is not connected to desktop startup. It must not run
multiple copies sharing one journal/client role.

## Offline verification

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.grok_learning_check
```

This exercises all five engines with synthetic comparisons, an 85%-win policy
that loses money, a positive-expectancy alternative, mocked nominations and
restart recovery. It makes no network request or order. Synthetic amounts are
not evidence about any real strategy.

To retain an existing raw comparison experiment and preview eligible candidates:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.grok_learning `
    path/to/raw-comparison-experiment.json `
    --journal data/research/learning/learning.sqlite3
```

The input must include original `engines` specifications and `results` rows.
The formatted output from `replay_comparison` is a summary and cannot substitute
for those rows. The CLI stays offline even when an API key is configured.
An explicitly constructed coordinator may call
`journal.review(experiment_id, client=shared_grok_client)` to request a review;
this consumes the existing client's API budget. There is no online CLI switch.

## Remaining learning loop

This delivers persistence and a bounded model review boundary. It does not
fine-tune Grok, dispatch paper trials, adapt live policies or rewrite code.
Next integration needs automatic complete-cohort outcome ingestion, strategy
and regime identity, source/commit and cost provenance, a trial scheduler,
holdout tracking, risk-adjusted portfolio evaluation, and a master dispatcher
that applies approved versions at explicit position boundaries. Selecting
from retrospective results is not independent forward validation. Repeatedly
optimizing a holdout consumes it.

Our October 13 loss audit also showed why ingestion must use actual lifecycle
buy/sell fills rather than blindly summing event P&L. Historical orders with
only hash identities need explicit classification; missing policy versions
must stay unknown. No accounting guess is added to learning memory here.

The [ORB source review](orb-source-review.md) records candidate external rules
and their evidence limits. Those projects are research inputs, not installed
engines or established profitable policies.
