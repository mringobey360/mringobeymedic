from django.test import SimpleTestCase

from .services import build_branch_analysis


def _branch(i, name, sales, profit, debt=0, credit=0, tx=10):
    return dict(branch_id=i, branch_name=name, location='', net_sales=str(sales), estimated_net_profit=str(profit),
                debt_outstanding=str(debt), credit_sales=str(credit), gross_sales=str(sales), returns_total='0',
                gross_profit=str(int(sales * 0.3)), expenses='0', transactions=tx)


class BranchAnalysisTests(SimpleTestCase):
    SUMMARY = dict(net_sales='1500000', estimated_net_profit='180000', debt_outstanding='300000')

    def test_single_branch_has_no_analysis(self):
        self.assertEqual(build_branch_analysis([_branch(1, 'A', 1000, 100)], self.SUMMARY), {})

    def test_branches_are_ranked_and_loss_making_branch_is_flagged(self):
        a, b = _branch(1, 'A', 1000000, 200000), _branch(2, 'B', 500000, -20000, debt=300000, credit=200000)
        result = build_branch_analysis([b, a], self.SUMMARY, [_branch(2, 'B', 800000, 0)])
        rows = result['rows']
        self.assertEqual([r['branch_name'] for r in rows], ['A', 'B'])
        self.assertEqual(rows[0]['status'], 'good')
        self.assertEqual(rows[1]['status'], 'alert')
        texts = ' '.join(f['text'] for f in rows[1]['flags'])
        self.assertIn('loss', texts)
        self.assertIn('credit', texts)
        self.assertIn('down', texts)          # 500k vs 800k the day before
        self.assertAlmostEqual(rows[0]['sales_share'], 66.7, delta=0.1)
        self.assertIn('Needs attention: B', result['headline'])
