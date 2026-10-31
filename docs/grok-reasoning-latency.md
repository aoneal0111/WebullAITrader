# Explicit execution reasoning effort and disconnected probe

Verified against xAI's official documentation on 2026-10-30:

- [Grok 4.7 model details](https://docs.x.ai/developers/models/grok-4.7)
- [Reasoning controls](https://docs.x.ai/developers/model-capabilities/text/reasoning)

The documented default for Grok 4.7 is high reasoning effort. Its supported
levels include low, medium, high and xhigh; reasoning cannot be disabled. On the
Responses API the control is `reasoning: {"effort": "low"}`. A low setting is
documented for latency-sensitive use, but does not guarantee any response time
or equal decision quality.

Atlas previously omitted the reasoning field. This is a plausible contributor
to latency, not proof that all timeouts are caused by model reasoning rather
than network/server conditions.

The explicit bounded execution suite now sets low effort for the exact verified
model IDs `grok-4.5`, `grok-4.6` and `grok-4.7`. Other models keep their default
and are reported as unverified for this setting. No model aliases are guessed.
The separate backtest reviewer, default non-pilot suite, output-token ceiling,
15-second transport timeout, quote checks and risk limits remain unchanged.
Request records now retain model and explicit effort so future comparisons can
identify what was actually requested. Existing records with no effort field do
not become measurements of a new configuration.

`tools/probe-grok-low-reasoning.ps1` prepares a separate pinned source checkout,
runs offline checks and makes one paid historical request through the current
shared twenty-attempt ledger. It neither installs code nor starts/stops Atlas.
The saved MASTER evidence is copied, retaining original assessment/candle times,
and labeled historical. A fresh probe request ID binds the structured answer.
The process has no order or position-management service. A selected candidate is
research output and cannot authorize a current trade.

Running the probe explicitly authorizes one disconnected research call, even
if the short entry lease has expired. It still requires the original parent
experiment to be unexpired, unchanged and unpaused, and spends from the SAME
shared budget. It never reactivates entries or changes their lease. This research
permission accepts only a LEARNING request with the fixed historical-probe
purpose and explicit non-execution flags; ordinary execution and backtest calls
retain their original lease checks. Paused, parent-expired, budget-exhausted and
unsupported configurations are refused. No budget reset, retry or timeout
extension is provided. The LEARNING cooldown also applies. The probe can run
while Atlas keeps protecting existing positions. A cooldown refusal is not a
provider timeout.

The small probe summary includes latency, validated-structure status, numeric
diagnostics, usage and a source-context hash. It omits symbols, prices, prompts,
answer text, credentials and trading performance. Detailed evidence stays in
the private local request ledger. A valid reply demonstrates one completed
historical schema request, not profitable behavior, current entry readiness or
a controlled benchmark of strategy quality. Repeated online measurements are
needed before declaring a dependable latency improvement.

After reviewing that result, the normal pinned execution installer can install
the low-effort execution profile. Normal application closure/restart is still
required for that separate installation; this probe does not change the running
Atlas client. Native protective exits remain independent throughout.
