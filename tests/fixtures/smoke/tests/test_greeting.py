import unittest

from greeting import greet


class GreetingTests(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_surrounding_whitespace(self):
        self.assertEqual(greet("  Ada  "), "Hello, Ada!")

    def test_internal_whitespace_is_preserved(self):
        self.assertEqual(greet(" Mary Ann "), "Hello, Mary Ann!")
