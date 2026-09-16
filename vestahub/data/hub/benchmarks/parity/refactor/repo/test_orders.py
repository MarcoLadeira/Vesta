import unittest

from orders import invoice_total, refund_total


class OrderTests(unittest.TestCase):
    def test_totals_preserve_tax_behavior(self):
        self.assertEqual(invoice_total(10), 12.0)
        self.assertEqual(refund_total(19.99), 23.99)


if __name__ == "__main__":
    unittest.main()
