# Warrior capture validation cost

The intelligence handoff latency regression times the complete `observe` call,
including synchronous capture construction. It is not an isolated measurement
of mailbox lookup or evidence that a slow callback holds the handoff lock.
Profiling found repeated sensitive-field scans and generic mapping checks in
capture construction on the caller path.

Capture validation now uses `collections.abc.Mapping`, a direct dictionary
fast path and a 256-entry cache of sensitive-name decisions. Only normalized
field names up to 64 characters enter the cache. Longer names are checked
directly. Every mapping, list and tuple is still traversed on every call;
values, serialized payloads, authority and execution decisions are not cached.
The forbidden-field substrings and canonical JSON/record identity are unchanged.

An alternating local microbenchmark of one captured decision payload measured
median record construction at 0.2900 ms before and 0.1678 ms after over seven
rounds of 3,000 constructions per implementation. This is a local component
measurement, not a whole-service latency guarantee or trading result.

Regression coverage exercises nested forbidden names after cache churn, long
forbidden names, custom mappings, changed unsupported values and exact serialized
evidence/record IDs. The existing full-observation 10 ms test and the percentile
budgets in the completed-bar benchmark retain their original assertions.
