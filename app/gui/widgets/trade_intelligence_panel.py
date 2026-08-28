from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import WatchlistRow


class TradeIntelligencePanel(QWidget):
    """Read-only explanation of one authoritative Atlas candidate row."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(488)
        self._last_row: WatchlistRow | None | object = object()
        self._render_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QFrame()
        header.setObjectName("tradeIntelligenceHeader")
        header.setMinimumHeight(78)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(6)
        candidate_line = QHBoxLayout()
        self._symbol = QLabel("--")
        self._symbol.setObjectName("candidateSymbol")
        self._symbol.setMinimumHeight(28)
        self._price = QLabel("--")
        self._price.setObjectName("candidatePrice")
        self._change = QLabel("--")
        self._change.setObjectName("candidateChange")
        candidate_line.addWidget(self._symbol)
        candidate_line.addWidget(self._price)
        candidate_line.addWidget(self._change)
        candidate_line.addStretch(1)
        header_layout.addLayout(candidate_line)

        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        self._header_metrics: dict[str, QLabel] = {}
        for name in (
            "Rank", "Scanner score", "Scanner status", "Warrior momentum",
            "Session", "Freshness"
        ):
            block = QVBoxLayout()
            title = QLabel(name.upper())
            title.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("headerMetricValue")
            block.addWidget(title)
            block.addWidget(value)
            metrics.addLayout(block)
            self._header_metrics[name] = value
        metrics.addStretch(1)
        header_layout.addLayout(metrics)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(8)
        watching, watching_layout = _section("WHY ATLAS IS WATCHING")
        self._reason = QLabel("Select an opportunity to inspect Atlas state.")
        self._reason.setWordWrap(True)
        self._reason.setObjectName("intelligenceExplanation")
        self._reason.setMinimumHeight(32)
        watching_layout.addWidget(self._reason)
        self._watching_values = _metric_grid(
            watching_layout,
            (
                "Relative volume", "Volume", "Dollar volume", "HOD distance",
                "Float", "Spread", "Catalyst", "Warrior score",
                "Warrior status", "Setup", "Warrior session",
            ),
            columns=3,
        )
        self._passed_rules = _fact_label(watching_layout, "PASSED RULES")
        self._failed_rules = _fact_label(watching_layout, "FAILED RULES")
        body.addWidget(watching, 1)

        decision, decision_layout = _section("CURRENT DECISION")
        self._decision = QLabel("--")
        self._decision.setObjectName("decisionState")
        self._decision.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._decision.setWordWrap(True)
        self._decision.setMinimumHeight(40)
        decision_layout.addWidget(self._decision)
        self._decision_explanation = QLabel("--")
        self._decision_explanation.setWordWrap(True)
        self._decision_explanation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        decision_layout.addWidget(self._decision_explanation)
        blocking_title = QLabel("BLOCKING THE TRADE")
        blocking_title.setObjectName("metricTitle")
        decision_layout.addWidget(blocking_title)
        self._blocking = QLabel("--")
        self._blocking.setWordWrap(True)
        self._blocking.setMinimumHeight(16)
        decision_layout.addWidget(self._blocking)
        decision_basis_title = QLabel("DECISION BASIS")
        decision_basis_title.setObjectName("metricTitle")
        decision_layout.addWidget(decision_basis_title)
        self._decision_basis = QLabel("--")
        self._decision_basis.setObjectName("monoValue")
        self._decision_basis.setWordWrap(True)
        self._decision_basis.setMinimumHeight(16)
        decision_layout.addWidget(self._decision_basis)
        self._decision_relation = QLabel("--")
        self._decision_relation.setObjectName("metricTitle")
        self._decision_relation.setWordWrap(True)
        decision_layout.addWidget(self._decision_relation)
        autonomous_title = QLabel("AUTONOMOUS PAPER")
        autonomous_title.setObjectName("metricTitle")
        decision_layout.addWidget(autonomous_title)
        self._autonomous_paper = QLabel("--")
        self._autonomous_paper.setObjectName("monoValue")
        self._autonomous_paper.setWordWrap(True)
        self._autonomous_paper.setMinimumHeight(16)
        decision_layout.addWidget(self._autonomous_paper)
        decision_layout.addStretch(1)
        market, market_layout = _section("CURRENT MARKET CONDITIONS")
        self._market_values = _metric_grid(
            market_layout,
            (
                "Last", "Bid", "Ask", "Spread", "Volume", "Relative volume",
                "Dollar volume", "Float", "HOD distance", "Session", "Freshness",
            ),
            columns=2,
        )
        body.addWidget(market, 1)

        plan, plan_layout = _section("TRADE PLAN")
        self._plan_values = _metric_grid(
            plan_layout,
            (
                "Strategy", "Setup", "Setup state", "Strategy status",
                "Entry trigger", "Stop",
            ),
            columns=2,
        )
        plan_layout.addStretch(1)
        body.addWidget(plan, 1)
        body.addWidget(decision, 1)
        body.setStretch(0, 24)
        body.setStretch(1, 22)
        body.setStretch(2, 26)
        body.setStretch(3, 28)
        self._watching = watching
        self._market = market
        self._plan = plan
        self._decision_panel = decision
        for panel in (watching, market, plan, decision):
            panel.setMinimumHeight(250)
        root.addLayout(body, 1)

    def render(self, row: WatchlistRow | None) -> None:
        if row == self._last_row:
            return
        self._last_row = row
        self._render_count += 1
        if row is None:
            self._render_empty()
            return

        _set_text(self._symbol, row.symbol)
        _set_text(self._price, _money_price(row.latest_price))
        _set_text(self._change, row.change_percent)
        _set_tone(self._change, _direction_tone(row.change_percent))
        header = {
            "Rank": f"#{row.rank}" if row.rank != "--" else "--",
            "Scanner score": row.score,
            "Scanner status": row.classification,
            "Warrior momentum": row.warrior_status,
            "Session": _first_available(row.warrior_session, row.session),
            "Freshness": row.freshness if row.freshness != "--" else row.stale,
        }
        _render_values(self._header_metrics, header)

        watching = {
            "Relative volume": row.relative_volume,
            "Volume": row.volume,
            "Dollar volume": row.dollar_volume,
            "HOD distance": row.distance_to_hod,
            "Float": row.float_shares,
            "Spread": row.spread,
            "Catalyst": row.catalyst,
            "Warrior score": row.warrior_score,
            "Warrior status": row.warrior_status,
            "Setup": row.setup,
            "Warrior session": row.warrior_session,
        }
        _render_values(self._watching_values, watching)
        _set_text(self._reason, row.explanations)
        _set_text(self._passed_rules, row.passed_rules)
        _set_text(self._failed_rules, row.failed_rules)

        decision, explanation, autonomous = _warrior_decision(row)
        _set_text(self._decision, decision)
        _set_tone(self._decision, _decision_tone(decision))
        _set_text(self._decision_explanation, explanation)
        _set_text(self._blocking, row.blocking_reasons)
        _set_text(self._decision_basis, _decision_basis(row))
        relation = _decision_market_relation(row)
        _set_text(self._decision_relation, relation)
        _set_tone(
            self._decision_relation,
            "warn" if relation == "CURRENT QUOTE NEWER THAN DECISION" else "neutral",
        )
        _set_text(self._autonomous_paper, autonomous)

        market = {
            "Last": _money_price(row.latest_price),
            "Bid": _money_price(row.bid),
            "Ask": _money_price(row.ask),
            "Spread": row.spread,
            "Volume": row.volume,
            "Relative volume": row.relative_volume,
            "Dollar volume": row.dollar_volume,
            "Float": row.float_shares,
            "HOD distance": row.distance_to_hod,
            "Session": _first_available(row.warrior_session, row.session),
            "Freshness": row.freshness if row.freshness != "--" else row.stale,
        }
        _render_values(self._market_values, market)
        plan = {
            "Strategy": row.strategy_name,
            "Setup": row.setup,
            "Setup state": row.setup_state,
            "Strategy status": row.strategy_status,
            "Entry trigger": row.entry_trigger,
            "Stop": row.stop_price,
        }
        _render_values(self._plan_values, plan)

    def _render_empty(self) -> None:
        _set_text(self._symbol, "--")
        _set_text(self._price, "--")
        _set_text(self._change, "--")
        _set_tone(self._change, "neutral")
        _render_values(self._header_metrics, {})
        _render_values(self._watching_values, {})
        _set_text(self._reason, "Select an opportunity to inspect Atlas state.")
        _set_text(self._passed_rules, "--")
        _set_text(self._failed_rules, "--")
        _set_text(self._decision, "--")
        _set_tone(self._decision, "neutral")
        _set_text(self._decision_explanation, "--")
        _set_text(self._blocking, "--")
        _set_text(self._decision_basis, "--")
        _set_text(self._decision_relation, "--")
        _set_tone(self._decision_relation, "neutral")
        _set_text(self._autonomous_paper, "--")
        _render_values(self._market_values, {})
        _render_values(self._plan_values, {})


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("intelligenceSection")
    frame.setMinimumWidth(0)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(10, 9, 10, 9)
    layout.setSpacing(6)
    heading = QLabel(title)
    heading.setObjectName("sectionTitle")
    layout.addWidget(heading)
    frame.heading = heading
    return frame, layout


def _metric_grid(
    parent: QVBoxLayout,
    names: tuple[str, ...],
    *,
    columns: int,
) -> dict[str, QLabel]:
    container = QWidget()
    grid = QGridLayout(container)
    grid.setContentsMargins(0, 2, 0, 2)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)
    values: dict[str, QLabel] = {}
    for index, name in enumerate(names):
        row, column = divmod(index, columns)
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(1)
        title = QLabel(name.upper())
        title.setObjectName("metricTitle")
        value = QLabel("--")
        value.setObjectName("monoValue")
        value.setWordWrap(True)
        value.setMinimumHeight(16)
        block_layout.addWidget(title)
        block_layout.addWidget(value)
        grid.addWidget(block, row, column)
        values[name] = value
    for column in range(columns):
        grid.setColumnStretch(column, 1)
    parent.addWidget(container)
    return values


def _fact_label(layout: QVBoxLayout, title: str) -> QLabel:
    heading = QLabel(title)
    heading.setObjectName("metricTitle")
    value = QLabel("--")
    value.setWordWrap(True)
    value.setMinimumHeight(16)
    layout.addWidget(heading)
    layout.addWidget(value)
    return value


def _render_values(labels: dict[str, QLabel], values: dict[str, str]) -> None:
    for name, label in labels.items():
        _set_text(label, values.get(name, "--"))


def _set_text(label: QLabel, value: str | None) -> None:
    text = value if value not in {None, ""} else "--"
    if label.text() != text:
        label.setText(text)


def _set_tone(label: QLabel, tone: str) -> None:
    if label.property("tone") == tone:
        return
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)


def _money_price(value: str) -> str:
    return value if value == "--" or value.startswith("$") else f"${value}"


def _first_available(*values: str) -> str:
    return next((value for value in values if value and value != "--"), "--")


def _direction_tone(value: str) -> str:
    return "good" if value.startswith("+") else "danger" if value.startswith("-") else "neutral"


def _decision_tone(value: str) -> str:
    normalized = value.upper()
    if any(word in normalized for word in ("BLOCK", "REJECT", "FAIL")):
        return "danger"
    if any(word in normalized for word in ("QUALIFY", "ARMED", "TRIGGER", "OPEN", "MANAGING")):
        return "good"
    if any(word in normalized for word in ("WAIT", "NEAR", "WATCH")):
        return "warn"
    return "neutral"


def _warrior_decision(row: WatchlistRow) -> tuple[str, str, str]:
    if not row.warrior_evaluated:
        return (
            row.warrior_status if row.warrior_status != "--" else "EVALUATING",
            "Warrior Momentum has not evaluated this selected symbol yet.",
            "WAITING",
        )

    status = row.warrior_status.upper()
    setup_state = row.setup_state.upper()
    if status == "ENTRY READY":
        return (
            "ENTRY READY",
            "Warrior Momentum has accepted the setup for PAPER execution.",
            "ENTRY READY",
        )
    if status == "AWAITING EXECUTION DATA":
        return (
            "WAITING FOR FRESH QUOTE",
            _blocking_explanation(row.blocking_reasons),
            "WAITING FOR FRESH QUOTE",
        )

    blockers = {
        value.strip().lower()
        for value in row.blocking_reasons.splitlines()
        if value.strip() and value.strip() != "--"
    }
    waiting_blockers = {
        "no warrior setup detected",
        "breakout is not confirmed",
        "entry trigger not reached",
    }
    if (
        setup_state == "TRIGGERED"
        or bool(blockers - waiting_blockers)
        or (status == "INELIGIBLE FOR EXECUTION" and row.setup != "NO SETUP")
    ):
        return "BLOCKED", _blocking_explanation(row.blocking_reasons), "BLOCKED"

    if row.setup == "NO SETUP":
        return (
            "WAIT",
            "Warrior Momentum has not detected a valid entry setup.",
            "WAITING FOR SETUP",
        )
    if setup_state in {"FORMING", "NOT FORMED", "UNKNOWN", "--"}:
        return (
            "WAIT",
            "Warrior Momentum is monitoring the setup; no PAPER order has been submitted.",
            "WAITING FOR SETUP",
        )
    return "WAIT", _first_available(row.explanations, row.blocking_reasons), "WAITING"


def _blocking_explanation(reasons: str) -> str:
    values = [value.strip().rstrip(".") for value in reasons.splitlines() if value.strip()]
    if not values:
        return "Warrior execution is blocked by an authoritative strategy gate."
    if len(values) == 1:
        return f"{values[0]}."
    return f"{'; '.join(values[:-1])}; and {values[-1].lower()}."


def _decision_basis(row: WatchlistRow) -> str:
    values = (
        row.decision_timestamp,
        row.decision_last,
        row.decision_bid,
        row.decision_ask,
        row.decision_spread,
    )
    if any(value in {None, "", "--"} for value in values):
        return "--"
    return "\n".join((
        _local_timestamp(row.decision_timestamp),
        f"Last {_money_price(row.decision_last)}  "
        f"Bid {_money_price(row.decision_bid)}  "
        f"Ask {_money_price(row.decision_ask)}",
        f"Spread {row.decision_spread}",
    ))


def _decision_market_relation(row: WatchlistRow) -> str:
    current = _aware_timestamp(row.market_timestamp)
    decision = _aware_timestamp(row.decision_timestamp)
    if current is None or decision is None:
        return "--"
    if current > decision:
        return "CURRENT QUOTE NEWER THAN DECISION"
    if current == decision:
        return "CURRENT QUOTE MATCHES DECISION"
    return "CURRENT QUOTE OLDER THAN DECISION"


def _local_timestamp(value: str) -> str:
    parsed = _aware_timestamp(value)
    if parsed is None:
        return "--"
    local = QDateTime.fromMSecsSinceEpoch(
        int(parsed.timestamp() * 1000)
    ).toLocalTime()
    return local.toString("HH:mm:ss t")


def _aware_timestamp(value: str) -> datetime | None:
    if value in {None, "", "--"}:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result


__all__ = ["TradeIntelligencePanel"]
