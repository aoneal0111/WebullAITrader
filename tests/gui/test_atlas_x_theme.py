from app.gui.theme import (
    Colors,
    Icons,
    Radius,
    Sizing,
    Spacing,
    Typography,
    application_stylesheet,
)


def test_theme_exposes_semantic_visual_tokens() -> None:
    assert Colors.BACKGROUND.startswith("#")
    assert Colors.SUCCESS != Colors.DANGER
    assert Typography.BODY > 0
    assert Spacing.XL > Spacing.SM
    assert Sizing.WINDOW_MIN_WIDTH > Sizing.SIDEBAR_MAX_WIDTH
    assert Radius.LG > Radius.SM
    assert Icons.START


def test_application_stylesheet_is_generated_from_theme() -> None:
    stylesheet = application_stylesheet()

    assert Colors.BACKGROUND in stylesheet
    assert Colors.SUCCESS in stylesheet
    assert "QSplitter::handle" in stylesheet
    assert "QStatusBar" in stylesheet

