# Warrior source-flow review and retained-price handling

The normalized stream distinguishes a volume-only snapshot borrowing a retained
price using trade_id snapshot-retained-price. The scanner adapter deliberately
does not refresh last_price_timestamp for that update. Warrior's subscribed
execution cache previously treated every TRADE payload as a new trade, including
this volume-only case. A fresh book plus a volume-only update could therefore
make the same old price appear current to execution confirmation while native
observation still correctly reported an old last trade. This is not evidence
that this defect caused all stale-quote rejections in a particular session.

Warrior's cache now ignores retained-price updates as trade confirmation. A real
priced trade or priced snapshot with its original provider timestamp can update
it normally. The regression first fails on the previous implementation, then
passes after the fix. Native Scalper selection, strategy parameters, timestamp
values, account controls and Grok review are unchanged.

warrior_quote_flow_review reads a previously exported closed review and its
original local journal. Archived manifest/summary bytes must match their source.
It checks every sequence, predecessor hash, baseline, canonical record hash,
final record/byte count and final chain head. It writes only after verification.
Memory is bounded by 512 symbol states and a few samples per fixed source/blocker
category. Output contains aggregate Warrior quote ages by stream/REST provenance,
market-event categories and comparisons to the latest genuine recorded stream
book/trade available in journal order. Whole journal segments are not copied.
An explicitly supplied runtime diagnostic is included only when its run ID and
SHA-256 match. It never selects the newest performance file automatically.

The reconstruction is diagnostic evidence, not replay execution authorization.
It cannot establish current subscription membership or recover absent raw SDK
fields. Freshness categories do not establish an executable setup or profitability.
Session-specific snapshot fields must not be remapped speculatively from stale
counters alone; source comparison is required first.

The PowerShell script leaves Atlas stopped and uses no provider/model/order
methods. Its ApplyRepair switch backs up and fast-forwards only after regression
checks and source verification. It changes no environment, operating spec, wallet
or ledger. External journals larger than the explicit review bounds are refused;
no completed result is fabricated from a partial source.
