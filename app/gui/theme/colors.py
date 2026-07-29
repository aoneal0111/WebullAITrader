from __future__ import annotations


class Colors:
    """Semantic color palette for every Atlas GUI surface."""

    # Application foundations
    BACKGROUND = "#080c12"
    BACKGROUND_ELEVATED = "#0b1119"
    BACKGROUND_INSET = "#06090e"

    # Layered surfaces
    SURFACE = "#0f1620"
    SURFACE_RAISED = "#151e2b"
    SURFACE_HOVER = "#1b2736"
    SURFACE_ACTIVE = "#202e40"
    SURFACE_INSET = "#0b1119"
    SURFACE_CARD = SURFACE
    SURFACE_PANEL = SURFACE_RAISED

    # Borders and separators
    BORDER = "#243244"
    BORDER_SUBTLE = "#1a2634"
    BORDER_STRONG = "#34475e"
    BORDER_INTERACTIVE = "#46617f"

    # Primary text hierarchy
    TEXT = "#eef4fb"
    TEXT_PRIMARY = TEXT
    TEXT_MUTED = "#8fa0b4"
    TEXT_SUBTLE = "#607086"
    TEXT_DISABLED = "#465467"
    TEXT_INVERSE = "#ffffff"

    # Primary interaction color
    ACCENT = "#3987f5"
    ACCENT_HOVER = "#5a9bfa"
    ACCENT_PRESSED = "#2c6dcc"
    ACCENT_MUTED = "#172b48"
    ACCENT_SUBTLE = "#102038"
    FOCUS = "#76adfa"

    # Semantic foreground colors
    SUCCESS = "#35c98b"
    WARNING = "#eeb64f"
    DANGER = "#ef6672"
    INFO = "#58b7e8"

    # Semantic background colors
    SUCCESS_MUTED = "#102a22"
    WARNING_MUTED = "#302717"
    DANGER_MUTED = "#321a21"
    INFO_MUTED = "#142936"

    # Trading semantics
    POSITIVE = SUCCESS
    NEGATIVE = DANGER
    NEUTRAL = TEXT_MUTED
    BUY = SUCCESS
    SELL = DANGER

    # Chart presentation
    CHART_BACKGROUND = BACKGROUND_INSET
    CHART_GRID = "#182331"
    CHART_AXIS = "#6f8197"
    CHART_CROSSHAIR = "#8295ac"
    CHART_VOLUME = "#355272"

    # Utility colors
    SHADOW = "rgba(0, 0, 0, 0.36)"
    OVERLAY = "rgba(8, 12, 18, 0.72)"
    OVERLAY_STRONG = "rgba(4, 7, 11, 0.86)"
