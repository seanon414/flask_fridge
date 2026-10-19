import unittest


class CanaryTests(unittest.TestCase):
    def test_this_fails_on_purpose(self):
        self.fail('deliberate failure to prove that CI blocks the merge')
