# Native offer timing review

The November 19 status confirmed one native review completed as MASTER_ABSTAINED,
no retained native review or uncertain dispatch, 158 post-REST stream selections,
and ten offers refused as NATIVE_SIGNAL_EXPIRED_OR_NOT_CURRENT. That combined
reason does not reveal which original timing condition failed. This evidence
capture does not change those conditions or claim their cause is already known.

`tools/capture-warrior-native-timing.ps1` creates an isolated read-only checkout,
runs its audit tests, and reads existing workstation data while Atlas may remain
running. It does not merge installed source, restart any runtime, call a provider
or model, change an order, extend a deadline or modify a strategy parameter.

The collector resolves only the configured local Warrior capture path from the
repository's configuration. It never exports environment values or model request
contexts. SQLite reads use mode=ro, query_only, bounded read transactions, a short
busy timeout and a three-second progress guard, including committed WAL contents.
The current recording pointer must match the operation spec and manifest.

For up to 64 recent refused native gates in the recording window it joins only an
exact symbol and lifecycle authorization transition within the preceding sixteen
serialized sequence numbers. The gate stores original signal time; its paired
transition stores evaluation time. Ambiguous or absent pairs remain unclassified.
The collector can prove a signal was future-dated or already expired at evaluation.
Otherwise it reports that the actual producer check clock is required; it does
not invent processing latency from a timestamp that was never recorded.

The export also includes up to 32 native model-call outcomes from this recording
window, with model reason and latency but without request contexts. Up to three
recent runtime diagnostic candidates expose native funnel counts, Warrior phase
timings and stream health. They are labeled candidates rather than asserted to be
an exact runtime solely because a file was newest. Read errors are distinguished
from zero results. Existing capture bytes, settings and runtime state are untouched.

Run against the same repository and persistent PAPER directory used by Atlas.
If a launcher separately overrides the capture path, the configured path may not
identify that runtime; an absent/unmatched pair is not evidence that no refusal
occurred. Upload the generated warrior-native-timing.json for the next diagnosis.
