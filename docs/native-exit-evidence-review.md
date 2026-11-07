# Native fill and exit evidence review

`NativeFillFunnel` records new canonical BUY fill facts for Warrior and Quick
Scalper in the entry funnel. It rejects pre-composition historical fill replay,
SELL events, non-native ownership, unpersisted fill IDs and duplicate callbacks.
Partial fill events and final fill events have separate counters; neither is a
count of unique closed trades. The hook is exception-contained and has no order
authority. A real desktop/gateway lifecycle test verifies the fill counter.

The V1 PAPER trial has a separate entry-routing mismatch: its Warrior family
allowlist includes research family IDs that the native `SetupType` engine does
not emit. Its intersection with the seven native executable setup names is
`HIGH_OF_DAY_BREAKOUT`. Adding names to an admission list does not install a
research detector or reproduce its backtested exits. The current research
families must not be relabeled as native early-entry adapters. This change
does not silently replace the running strategy or admit additional families.

`capture-closed-exit-trace.ps1` uses a separate checkout. It validates the full
closed daily journal chain, its final receipt and session baseline, then exports
operator-selected symbol contexts from two minutes before entry through two minutes
after exit. It uses the supplied closed review's audit and pointer, rather than
guessing the newest session. Source files, installed code, credentials and trial
budgets are unchanged. No data provider, Grok or order calls occur.

All matching PAPER events are eligible for export. Non-order contexts are
sampled at most once per second per kind and symbol. Output is capped at 8 MiB
and 12,000 records, with explicit truncation status. Selected records retain
their original source hashes but do not form a standalone full chain. This is
diagnostic evidence, not a full MFE or executable BBO replay. The export is
private and must not be committed. Determine exit reasons, stop placement and
amendment history before attributing an oversized realized loss to a particular
protection defect or changing profit-taking rules.
