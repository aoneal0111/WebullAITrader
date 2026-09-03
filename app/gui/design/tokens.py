from __future__ import annotations

class Colors:
    BACKGROUND = "#07111f"
    SIDEBAR = "#081321"
    SURFACE = "#0b1626"
    SURFACE_ALT = "#0e1b2d"
    SURFACE_RAISED = "#122137"
    SURFACE_HOVER = "#142742"
    BORDER = "#1c2a3d"
    BORDER_SOFT = "#142238"
    BORDER_STRONG = "#2b405c"
    TEXT = "#f3f7fc"
    TEXT_STRONG = "#ffffff"
    TEXT_MUTED = "#aab6c5"
    TEXT_FAINT = "#718096"
    ACCENT = "#1677ff"
    CYAN = "#20b8ff"
    ACCENT_SOFT = "#102c50"
    ACCENT_HOVER = "#4093ff"
    SUCCESS = "#23d18b"
    SUCCESS_SOFT = "#102b24"
    WARNING = "#f7c843"
    WARNING_SOFT = "#302612"
    DANGER = "#ff4d5a"
    DANGER_SOFT = "#34171d"
    CHART_GRID = "#172231"

class Spacing:
    XS = 4
    SM = 8
    MD = 13
    LG = 18
    XL = 24
    XXL = 32

class Radius:
    SM = 4
    MD = 6
    LG = 8


class Typography:
    # Qt generic families are portable across Windows, offscreen test
    # platforms, and installations without the optional bundled fonts.
    FAMILY = "Sans Serif"
    MONO = "Monospace"
    XS = 10
    SM = 11
    MD = 11
    LG = 14
    XL = 18
    XXL = 20
    PANEL_TITLE = 12
    PRIMARY_METRIC = 20


class Dimensions:
    NAV_WIDTH = 184
    NAV_COMPACT_WIDTH = 104
    HEADER_HEIGHT = 102
    STATUS_HEIGHT = 24
    TABLE_ROW_HEIGHT = 27
    SPLITTER_HANDLE_WIDTH = 7
    # The market canvas is the primary laptop surface. The dashboard scrolls
    # vertically instead of negotiating it down to dashboard-card size.
    CHART_MIN_HEIGHT = 400
    OPERATOR_MIN_HEIGHT = 158
    WATCHLIST_MIN_WIDTH = 420
