import unittest

from session import expired


class SessionTests(unittest.TestCase):
    # [utest->req~session-expiration~1]
    def test_expiration(self):
        self.assertFalse(expired(1799))
        self.assertTrue(expired(1800))
