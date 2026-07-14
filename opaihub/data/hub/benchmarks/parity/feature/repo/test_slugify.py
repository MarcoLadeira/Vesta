import unittest

from slugify import is_valid_slug, slugify


class SlugTests(unittest.TestCase):
    def test_slugify_and_validation(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertTrue(is_valid_slug("hello-world"))
        for value in ("", "Hello", "two--parts", "-edge", "edge-"):
            self.assertFalse(is_valid_slug(value), value)


if __name__ == "__main__":
    unittest.main()
