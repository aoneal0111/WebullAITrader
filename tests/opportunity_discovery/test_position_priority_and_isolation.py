import ast
from datetime import timedelta

from app.opportunity_discovery import (
    PositionFocusTier,
    ResearchFocusSubject,
    prioritize_research_focus,
)
from tests.opportunity_discovery.test_position_continuity import (
    membership,
    opened_position,
    opportunity,
)
from app.opportunity_discovery import observe_position_opportunity


def test_position_focus_outranks_orders_opportunities_and_scanner_rank():
    focused = prioritize_research_focus((
        ResearchFocusSubject("scanner-rank-1", "OTHER", PositionFocusTier.SCANNER_DISCOVERY),
        ResearchFocusSubject("scanner-rank-999", "ABCD", PositionFocusTier.SCANNER_DISCOVERY),
        ResearchFocusSubject("forming", "ABCD", PositionFocusTier.FORMING_OPPORTUNITY),
        ResearchFocusSubject("triggered", "ABCD", PositionFocusTier.TRIGGERED_OPPORTUNITY),
        ResearchFocusSubject("working-order", "ABCD", PositionFocusTier.WORKING_ORDER),
        ResearchFocusSubject("paper-position:ABCD:1", "ABCD", PositionFocusTier.OPEN_POSITION),
    ))
    abcd = next(item for item in focused if item.symbol == "ABCD")
    assert abcd.tier is PositionFocusTier.OPEN_POSITION
    assert focused[0] == abcd


def test_position_remains_focused_after_scanner_candidate_exit():
    subjects = (
        ResearchFocusSubject("paper-position:ABCD:1", "ABCD", PositionFocusTier.OPEN_POSITION),
        ResearchFocusSubject("scanner:OTHER", "OTHER", PositionFocusTier.SCANNER_DISCOVERY),
    )
    focused = prioritize_research_focus(subjects)
    assert [item.symbol for item in focused] == ["ABCD", "OTHER"]


def test_long_lived_position_bridges_many_windows_without_duplicate_position():
    projection, _ = opened_position()
    position_ids = set()
    for minute in range(1, 66):
        strategy = "FIRST_PULLBACK" if minute % 2 else "HIGHER_LOW_CONTINUATION"
        current = opportunity(f"opp:window:{minute // 15}", minute, membership(strategy))
        projection = observe_position_opportunity(projection, current)
        position_ids.add(projection.position_id)
    for scanner_rank in range(3_000):
        focused = prioritize_research_focus((
            ResearchFocusSubject(projection.position_id, "ABCD", PositionFocusTier.OPEN_POSITION),
            ResearchFocusSubject(f"scanner:{scanner_rank}", "ABCD", PositionFocusTier.SCANNER_DISCOVERY),
        ))
        assert focused[0].subject_id == projection.position_id
    assert position_ids == {"paper-position:ABCD:1"}
    assert projection.correlated_opportunity_ids == (
        "opp:entry", "opp:window:0", "opp:window:1", "opp:window:2",
        "opp:window:3", "opp:window:4",
    )
    assert len(projection.strategy_transition_history) > 60


def test_position_research_module_has_no_mutation_or_execution_calls():
    source = open("app/opportunity_discovery/position_continuity.py", encoding="utf-8").read()
    tree = ast.parse(source)
    forbidden = {
        "place_order", "cancel_order", "replace_order", "authorize_order",
        "resize_position", "close_position", "submit_order", "submit_exit",
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    imports = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not calls & forbidden
    assert not any(any(term in name for term in ("broker", "gateway", "order_service", "account_mutation")) for name in imports)


def test_transition_has_no_quantity_stop_or_position_authority():
    projection, _ = opened_position()
    before = (
        projection.position_id, projection.entry_strategy_id,
        projection.initial_structural_stop, projection.initial_risk,
    )
    later = opportunity("opp:later", 20, membership("BREAKOUT_RETEST_CONTINUATION"))
    after = observe_position_opportunity(projection, later)
    assert (
        after.position_id, after.entry_strategy_id,
        after.initial_structural_stop, after.initial_risk,
    ) == before
    assert not any(hasattr(item, name) for item in after.strategy_transition_history for name in (
        "quantity", "order", "exit_authorized", "position_mutation",
    ))
