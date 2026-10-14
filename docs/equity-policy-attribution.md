# New equity trade policy attribution

Desktop composition installs passive attribution for Warrior and Quick Scalper.
New prepared/native entries record strategy family, frozen policy version,
engine configuration fingerprint, declared-source fingerprint and attribution
scope. This changes metadata only, not entry rules, sizing, order prices or
protective exits. No model call is added.

Warrior prefers the explicit primary taxonomy strategy; otherwise it records
the native setup type. It does not select an arbitrary overlapping membership.
Scalper records its actual quick momentum policy family. Missing family,
configuration or source information leaves the version `UNKNOWN`.

The version combines engine, family, configuration and the explicitly listed
source files in `equity_policy_attribution.py`. Source hashes are computed once
at desktop construction, normalized for Windows CRLF. Per-trade symbol, prices,
timestamps, risk dollars, order IDs and metadata hashes are not version inputs.
This is a declared policy-source scope, not a full repository commit or complete
dependency fingerprint. It must not be presented as proof of identical feeds,
execution environment or market regime. Restart after policy code/config edits;
hot-reloading policy code is not supported by this attribution.

Prepared orders retain their stamp through the shared controller and durable
book. Replacements, add-ons and exits inherit the original lifecycle's stamp;
they do not acquire the current provider's version. Older unstamped positions
stay unstamped. Callback failures fall back to missing attribution and do not
introduce new execution gates. No historical row is rewritten or backfilled.
This is entry-policy attribution, not an enforcement mechanism for policy
migration. It does not prove that management code stayed unchanged across a
restart with an open position; that requires separate management-version
tracking before treating such outcomes as fixed-policy execution evidence.

The learning importer accepts an optional strategy-family field for new stamped
outcomes. Previously imported legacy outcomes preserve their original payload,
so a repeat import does not conflict or count them twice. Feedback groups now
show family beside policy version. Conflicting filled-entry families are
excluded, rather than blended into one apparent strategy result.

This supports attribution, not strategy profitability. The historical 132-trade
cohort remains on unknown policy versions and cannot be assigned to today's
rules. Additional source/market-data provenance, complete trial comparisons,
other asset adapters, desktop ingestion and controlled policy activation still
need separate work. Grok remains inactive without its configured key/model and
explicit activation.
