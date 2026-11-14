"""Read-only attribution of existing simulated fills; no backtest or trading API."""
import argparse
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path

D = Decimal
SOURCES = {
    'kraken': 'https://support.kraken.com/articles/cross-platform-fee-tier-changes',
    'webull': 'https://www.webull.com/help/faq/11091-Fees-Limits',
}


def number(value):
    try:
        result = D(str(value))
    except InvalidOperation as exc:
        raise ValueError('INVALID_NUMBER') from exc
    if not result.is_finite():
        raise ValueError('NONFINITE_NUMBER')
    return result


def cost_floor(kind, buy, sell):
    """Underlying price return needed to recover entry and exit friction."""
    buy, sell = number(buy), number(sell)
    if not (0 <= buy < 1 and 0 <= sell < 1):
        raise ValueError('INVALID_COST_RATE')
    if kind == 'COMMISSION':
        return (1 + buy) / (1 - sell) - 1
    if kind == 'EMBEDDED_MARKUP_MARKDOWN':
        return (1 + buy) / (1 - sell) - 1
    raise ValueError('UNKNOWN_COST_MODEL')


def attribute(trades):
    gross = costs = net = D(0)
    wins, losses = [], []
    reasons = {}
    for t in trades:
        amount, opened, closed = (number(t[k]) for k in ('amount', 'open_rate', 'close_rate'))
        entry, exit_ = (number(t[k]) for k in ('fee_open', 'fee_close'))
        if (t.get('is_short', False) or number(t.get('leverage', 1)) != 1
                or amount <= 0 or opened <= 0 or closed <= 0
                or not (0 <= entry < 1 and 0 <= exit_ < 1)):
            raise ValueError('ONLY_UNLEVERED_LONG_SPOT_SUPPORTED')
        raw = amount * (closed - opened)
        cost = amount * opened * entry + amount * closed * exit_
        realized = number(t['profit_abs'])
        # Native exported quantities/prices are rounded; do not silently accept
        # added-position, funding, or other unreconciled accounting.
        if abs(raw - cost - realized) > D('0.0001'):
            raise ValueError('FILL_ACCOUNTING_DOES_NOT_RECONCILE')
        gross += raw; costs += cost; net += realized
        if realized > 0: wins.append(realized)
        elif realized < 0: losses.append(-realized)
        reason = str(t['exit_reason'])
        item = reasons.setdefault(reason, {'count': 0, 'net': D(0)})
        item['count'] += 1; item['net'] += realized
    avg_win = sum(wins, D(0)) / len(wins) if wins else None
    avg_loss = sum(losses, D(0)) / len(losses) if losses else None
    breakeven = avg_loss / (avg_loss + avg_win) if avg_win and avg_loss else None
    return {'trades': len(trades), 'gross_fill_profit': gross,
        'modeled_commissions': costs, 'reported_net': net,
        'accounting_residual': net - (gross - costs),
        'average_net_win': avg_win, 'average_net_loss': avg_loss,
        'break_even_win_rate_at_observed_payoffs': breakeven,
        'exit_reasons': reasons}


def analyze(root):
    root = Path(root).resolve()
    summary_path = root / 'candidate-screen-summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    if summary.get('scope') != 'OFFLINE_CRYPTO_REPOSITORY_CANDIDATE_SCREEN_V1' or summary.get('status') != 'COMPLETE':
        raise ValueError('COMPLETE_CANDIDATE_SCREEN_REQUIRED')
    rows = []
    for metrics in summary['development']:
        name = metrics['candidate'] + '-' + metrics['exit_variant'] + '-' + str(int(number(metrics['fee_per_side']) * 10000)) + '-development'
        path = root / name / 'native-result.json'
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('RESULT_PATH_OUTSIDE_SCREEN')
        raw = path.read_bytes(); result = json.loads(raw)
        attribution = attribute(result['trades'])
        if (attribution['trades'] != metrics['total_trades'] or
                abs(attribution['reported_net'] - number(metrics['profit_total_abs'])) > D('0.0001')):
            raise ValueError('SUMMARY_RESULT_MISMATCH')
        rows.append({'candidate': metrics['candidate'], 'exit_variant': metrics['exit_variant'],
            'fee_per_side': metrics['fee_per_side'], 'native_result_sha256': sha256(raw).hexdigest(),
            **attribution})
    return {'scope': 'OFFLINE_CRYPTO_COST_ATTRIBUTION_V1',
        'source_summary_sha256': sha256(summary_path.read_bytes()).hexdigest(),
        'network_requests': 0, 'orders': 0, 'grok_requests': 0,
        'baseline_modified': False, 'promotion': 'NONE', 'account_tier_verified': False,
        'public_sources_checked_on': '2026-10-28', 'public_sources': SOURCES,
        'illustrative_cost_floors': [
            {'profile': label, 'kind': kind, 'buy_rate': rate, 'sell_rate': rate,
             'underlying_return_to_break_even': cost_floor(kind, rate, rate)}
            for label, kind, rate in [
                ('PREVIOUS_LAB_BASE', 'COMMISSION', '.004'),
                ('PREVIOUS_LAB_STRESS', 'COMMISSION', '.006'),
                ('KRAKEN_CURRENT_TIER1_TAKER', 'COMMISSION', '.008'),
                ('WEBULL_PUBLIC_CRYPTO_HELP', 'EMBEDDED_MARKUP_MARKDOWN', '.01')]],
        'development_attribution': rows,
        'limitations': [
            'Current public schedules do not verify the operator account or historical fee tier',
            'Kraken archive candles are not Webull executable quotes',
            'Cost floors exclude additional market spread, slippage, and queue effects',
            'Embedded Webull spread must not be charged twice if already in executable quotes',
            'No results repriced: changed costs can change ROI exits, signals accepted, and trade sequence',
            'Break-even win rate uses observed payoffs; it is descriptive, not a forecast',
            'Development fills only; no September performance validation opened']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = analyze(args.screen)
    with Path(args.output).open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, default=lambda x: str(x) if isinstance(x, D) else None, allow_nan=False)
    print('COST_ATTRIBUTION=COMPLETE; REPLAY=NOT_RUN; PROMOTION=NONE')
    print('UPLOAD_SUMMARY=' + str(Path(args.output).resolve()))


if __name__ == '__main__':
    main()
