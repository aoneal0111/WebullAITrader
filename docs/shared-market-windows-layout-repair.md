# Shared market dashboard minimum height repair

The shared-workspace installer at e7d05a8 refused installation on Windows
because the scanner was 372px high while the position panel required 395px at
1536×1024. The original populated-layout regression remains unchanged.

Mission Control now uses an eight-field position summary: side/quantity,
average entry, unrealized and realized P/L, current canonical stop, next target,
management state, and strategy/setup. This reduces the native-font minimum
height of the management grid. The positions table remains available, and the
separate Positions page retains the full detail grid. All market dashboards
use the same compact summary.

Added regressions check selected-position values, containment, scanner space,
and compact versus full details with 10, 12, and 14 point fonts. These offscreen
checks do not substitute for the Windows installer's own checks.

The installer is reused with the corrected revision. This change does not alter
trading parameters, operating specifications, ledgers, launchers, or crypto workers.
