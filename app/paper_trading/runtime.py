from decimal import Decimal, InvalidOperation

from app.execution_planner import ExecutionPlanDecision, ExecutionInstruction
from app.order_placement import OrderSide, OrderType
from app.paper_trading.exceptions import (PaperTradingDependencyError, PaperTradingEvaluationError,
                                          PaperTradingValidationError)
from app.paper_trading.milestone_models import (ZERO, PaperExecutionOutcome, PaperExecutionResult, PaperFill,
                                      PaperOrder, PaperOrderStatus, PaperPortfolioSnapshot, PaperPosition,
                                      PaperTradingAccount, PaperTradingCriteriaResult)
from app.paper_trading.validation import validate_dependencies, validate_request


class CompletePaperFillEvaluator:
    """Deterministic evaluator that proposes the complete requested quantity."""
    def evaluate(self, request, instruction, account, market_price, policy):
        return instruction.quantity


class PaperTradingRuntime:
    def __init__(self, evaluator, policy):
        validate_dependencies(evaluator, policy)
        self._evaluator = evaluator
        self._policy = policy

    def execute(self, request):
        request = validate_request(request)
        account = request.account
        if not self._policy.enabled:
            return self._result(request, PaperExecutionOutcome.DISABLED, account, None, None, (False, False, False), "paper trading disabled")
        plan_result = request.execution_plan_result
        if plan_result.decision is not ExecutionPlanDecision.PLANNED:
            no_action = plan_result.metadata.get("no_action") is True or plan_result.metadata.get("signal") == "HOLD"
            outcome = PaperExecutionOutcome.NO_ACTION if no_action else PaperExecutionOutcome.REJECTED
            return self._result(request, outcome, account, None, None, (True, False, False), "execution plan is not actionable")
        if plan_result.plan is None or len(plan_result.plan.instructions) != 1:
            return self._result(request, PaperExecutionOutcome.REJECTED, account, None, None, (True, False, False), "plan must contain exactly one instruction")
        instruction = plan_result.plan.instructions[0]
        mismatch = (request.account_id != account.account_id or instruction.account_id != request.account_id)
        if mismatch:
            return self._rejected(request, instruction, account, "account identity mismatch", (True, True, False))
        position = next((x for x in account.positions if x.symbol == instruction.symbol), None)
        if position is not None and position.account_id != request.account_id:
            return self._rejected(request, instruction, account, "position account identity mismatch", (True, True, False))
        if not self._price_executable(instruction, request.market_price):
            return self._rejected(request, instruction, account, "order price conditions not met", (True, True, False))
        try:
            proposed = self._evaluator.evaluate(request, instruction, account, request.market_price, self._policy)
        except Exception as exc:
            raise PaperTradingEvaluationError("paper fill evaluator failed") from exc
        fill_quantity = self._quantity(proposed)
        if fill_quantity <= 0:
            return self._rejected(request, instruction, account, "evaluator proposed zero fill", (True, True, False))
        if fill_quantity > instruction.quantity:
            raise PaperTradingDependencyError("fill evaluator proposed quantity above requested quantity")
        if fill_quantity < instruction.quantity and not self._policy.allow_partial_fills:
            return self._rejected(request, instruction, account, "partial fills are disabled", (True, True, False))
        execution_price = self._execution_price(instruction.side, request.market_price)
        fees = self._policy.commission_per_order + self._policy.commission_per_share * fill_quantity
        if not execution_price.is_finite() or execution_price <= 0 or not fees.is_finite() or fees < 0:
            return self._rejected(request, instruction, account, "adjusted price or fees are invalid", (True, True, False))
        gross = execution_price * fill_quantity
        if instruction.side is OrderSide.BUY and account.cash < gross + fees:
            return self._rejected(request, instruction, account, "insufficient cash", (True, True, False))
        if instruction.side is OrderSide.SELL and (position is None or position.quantity < fill_quantity):
            return self._rejected(request, instruction, account, "sell quantity exceeds held quantity", (True, True, False))
        order_id, fill_id = request.request_id + ":order", request.request_id + ":fill"
        status = PaperOrderStatus.FILLED if fill_quantity == instruction.quantity else PaperOrderStatus.PARTIALLY_FILLED
        order = PaperOrder(order_id, request.request_id, request.account_id, instruction.symbol, instruction.side,
                           instruction.order_type, instruction.quantity, fill_quantity, status, execution_price, fees,
                           request.execution_timestamp, metadata=request.metadata)
        fill = PaperFill(fill_id, order_id, request.request_id, request.account_id, instruction.symbol, instruction.side,
                         fill_quantity, execution_price, gross, fees, request.execution_timestamp, request.metadata)
        new_account = self._transition(account, instruction, position, fill, order)
        outcome = PaperExecutionOutcome.EXECUTED if status is PaperOrderStatus.FILLED else PaperExecutionOutcome.PARTIALLY_EXECUTED
        return self._result(request, outcome, new_account, order, fill, (True, True, True), "paper fill applied")

    @staticmethod
    def _quantity(value):
        if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
            raise PaperTradingDependencyError("fill evaluator must return a Decimal-compatible quantity")
        try: result = Decimal(value)
        except (InvalidOperation, ValueError) as exc: raise PaperTradingDependencyError("fill evaluator returned invalid quantity") from exc
        if not result.is_finite(): raise PaperTradingDependencyError("fill evaluator returned non-finite quantity")
        return result

    def _execution_price(self, side, market_price):
        adjustment = self._policy.slippage_basis_points / Decimal("10000")
        return market_price * (Decimal("1") + adjustment if side is OrderSide.BUY else Decimal("1") - adjustment)

    @staticmethod
    def _price_executable(instruction: ExecutionInstruction, market_price):
        if instruction.order_type is OrderType.MARKET: return True
        limit_ok = (instruction.limit_price is None or
                    (market_price <= instruction.limit_price if instruction.side is OrderSide.BUY else market_price >= instruction.limit_price))
        stop_ok = (instruction.stop_price is None or
                   (market_price >= instruction.stop_price if instruction.side is OrderSide.BUY else market_price <= instruction.stop_price))
        if instruction.order_type is OrderType.LIMIT: return limit_ok
        if instruction.order_type is OrderType.STOP: return stop_ok
        return limit_ok and stop_ok

    def _transition(self, account, instruction, old_position, fill, order):
        positions = [x for x in account.positions if x.symbol != instruction.symbol]
        if instruction.side is OrderSide.BUY:
            old_quantity = old_position.quantity if old_position else ZERO
            old_cost = old_position.average_cost * old_quantity if old_position else ZERO
            quantity = old_quantity + fill.quantity
            average_cost = (old_cost + fill.gross_amount + fill.fees) / quantity
            realized = old_position.realized_profit_loss if old_position else ZERO
            position = PaperPosition(account.account_id, instruction.symbol, quantity, average_cost, fill.price,
                                     quantity * fill.price, (fill.price - average_cost) * quantity, realized,
                                     old_position.metadata if old_position else {})
            positions.append(position); cash = account.cash - fill.gross_amount - fill.fees
            total_realized = account.realized_profit_loss
        else:
            realized_delta = (fill.price - old_position.average_cost) * fill.quantity - fill.fees
            realized = old_position.realized_profit_loss + realized_delta
            quantity = old_position.quantity - fill.quantity
            if quantity > 0:
                positions.append(PaperPosition(account.account_id, instruction.symbol, quantity, old_position.average_cost,
                                               fill.price, quantity * fill.price, (fill.price - old_position.average_cost) * quantity,
                                               realized, old_position.metadata))
            cash = account.cash + fill.gross_amount - fill.fees; total_realized = account.realized_profit_loss + realized_delta
        positions.sort(key=lambda x: x.symbol)
        market_value = sum((x.market_value for x in positions), ZERO); unrealized = sum((x.unrealized_profit_loss for x in positions), ZERO)
        return PaperTradingAccount(account.account_id, cash, cash, tuple(positions), account.orders + (order,), account.fills + (fill,),
                                   total_realized, unrealized, market_value, cash + market_value, account.metadata)

    def _rejected(self, request, instruction, account, reason, passed):
        order = PaperOrder(request.request_id + ":order", request.request_id, request.account_id, instruction.symbol,
                           instruction.side, instruction.order_type, instruction.quantity, ZERO, PaperOrderStatus.REJECTED,
                           None, ZERO, request.execution_timestamp, reason, request.metadata)
        # Rejected execution leaves the supplied account byte-for-byte unchanged; the order is returned as event output only.
        return self._result(request, PaperExecutionOutcome.REJECTED, account, order, None, passed, reason)

    def _result(self, request, outcome, account, order, fill, passed, detail):
        snapshot = PaperPortfolioSnapshot(account.account_id, account.cash, account.buying_power, account.positions,
                                          account.realized_profit_loss, account.unrealized_profit_loss,
                                          account.total_market_value, account.total_equity, request.execution_timestamp)
        names = ("policy_enabled", "plan_eligible", "execution_applied")
        criteria = tuple(PaperTradingCriteriaResult(name, value, detail) for name, value in zip(names, passed))
        return PaperExecutionResult(request.request_id, request.account_id, outcome, account, order, fill, snapshot,
                                    criteria, self._policy.version, {"deterministic": True})
