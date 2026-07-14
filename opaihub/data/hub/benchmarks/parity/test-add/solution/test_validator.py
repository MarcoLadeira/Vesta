import unittest

from validator import is_even


class ValidatorTests(unittest.TestCase):
    def test_even_values_include_positive_negative_and_zero(self):
        for value in (2, -4, 0):
            self.assertTrue(is_even(value), value)

    def test_odd_values_include_positive_and_negative(self):
        for value in (1, -3):
            self.assertFalse(is_even(value), value)


if __name__ == "__main__":
    unittest.main()
