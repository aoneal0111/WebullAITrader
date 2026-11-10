import unittest
from app.strategy_lab.joint_entry_exit_search import passes


class JointSearchGates(unittest.TestCase):
    def row(self):
        return dict(closed_trades=30,win_rate=.7,terminal_net_after_overhead=100,
            entry_active_days=14,calendar_days=14,terminal_forced_closes=1,maximum_drawdown=.1)

    def test_high_closed_win_rate_cannot_hide_terminal_losses(self):
        row=self.row();row.update(win_rate=1.,terminal_net_after_overhead=-20)
        self.assertFalse(passes(row))

    def test_daily_activity_and_costs_are_independent_requirements(self):
        self.assertTrue(passes(self.row()))
        for key,value in [('entry_active_days',13),('closed_trades',19),('win_rate',.6),
                          ('terminal_net_after_overhead',0),('maximum_drawdown',.2),('terminal_forced_closes',20)]:
            row=self.row();row[key]=value;self.assertFalse(passes(row),key)


if __name__=='__main__':unittest.main()
