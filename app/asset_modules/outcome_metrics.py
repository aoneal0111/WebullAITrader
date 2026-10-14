"""Recorded-PnL descriptive metrics, never policy eligibility or account returns."""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext


def outcome_metrics(pnls):
    if not isinstance(pnls, (list, tuple)) or len(pnls) > 100000:
        raise ValueError("Bounded closed-outcome amounts required")
    if any(not isinstance(p, Decimal) or not p.is_finite()
           or len(p.as_tuple().digits) > 100 or not -100 <= p.as_tuple().exponent <= 12
           or p.copy_abs() > Decimal('1e12') for p in pnls):
        raise ValueError("Finite bounded Decimal outcomes required")
    positive, negative = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 150, ROUND_HALF_EVEN
        total = sum(pnls, Decimal(0))
        gains = sum(positive, Decimal(0))
        losses = sum(negative, Decimal(0))
    worst = min(negative) if negative else None
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 28, ROUND_HALF_EVEN
        return dict(net_pnl=str(total), wins=len(positive), losses=len(negative),
            breakeven=len(pnls)-len(positive)-len(negative),
            win_rate=str(Decimal(len(positive))/len(pnls)) if pnls else None,
            mean_net_pnl=str(total/len(pnls)) if pnls else None,
            average_win_pnl=str(gains/len(positive)) if positive else None,
            average_loss_pnl=str(losses/len(negative)) if negative else None,
            gross_winning_pnl=str(gains), gross_losing_pnl=str(losses),
            profit_factor=str(gains/losses.copy_abs()) if negative else None,
            profit_factor_state="DEFINED" if negative else "NO_LOSSES" if pnls else "NO_CLOSED_OUTCOMES",
            largest_loss_pnl=str(worst) if worst is not None else None,
            largest_loss_share=str(worst/losses) if worst is not None else None,
            expectancy_unit="RECORDED_NET_PNL_PER_CLOSED_OUTCOME")
