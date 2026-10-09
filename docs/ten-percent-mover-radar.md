# Retained ten-percent mover observation

Desktop discovery now retains a stock for one hour after a fresh screener
observation shows a gain of at least 10 percent. Each fresh qualifying observation
renews the window. Falling below 10 percent or leaving the source ranking does
not immediately remove the mover. Reusing an old discovery row does not renew
the window. The retained set is bounded to 500 symbols and resets at the next
Eastern market session or trading date; it is not persisted across restarts.

Webull change ratios are converted to percentage points before applying the
threshold. An explicit change_percent takes precedence. When neither is present,
price versus pre_close can establish the percentage; a dollar change alone cannot.

The existing top-50 gainers and relative-volume discovery lanes remain available.
Retained rows do not pretend to retain their old position in today's live ranking.
Additional radar promotion requires the 10-percent move. Qualified movers that
leave the ranking compete in the bounded accelerator lane. A retained mover may
be prioritized by the existing catalyst discovery sidecar. News alone does not
satisfy the 10-percent requirement for this lane. If that sidecar is unavailable,
price-based observation continues without a fabricated news signal.

This changes observation selection only. It does not submit an order or change
Warrior/scalper entry, freshness, structural-stop, risk, or exit rules. Position
management keeps its existing subscription priority. Capacity can prevent every
retained symbol from receiving simultaneous streaming subscriptions.

Provider diagnostics expose retained_ten_percent_movers; admission diagnostics
identify RETAINED_TEN_PERCENT_MOVER. A separate top-50 GUI view and earlier
intrabar entry rules are not part of this patch.
