import unittest

from app.scripts.control_logic import decide


class DecideTests(unittest.TestCase):
    def d(self, temp, is_on, since=9999, target=45, hyst=1.0, cycle=300):
        return decide(temp, target, hyst, is_on, since, cycle)

    def test_turns_on_above_band(self):
        self.assertTrue(self.d(46.0, False))

    def test_stays_off_inside_band(self):
        self.assertFalse(self.d(45.5, False))

    def test_stays_on_inside_band(self):
        self.assertTrue(self.d(44.5, True))

    def test_turns_off_below_band(self):
        self.assertFalse(self.d(44.0, True))

    def test_min_cycle_blocks_change(self):
        self.assertFalse(self.d(50.0, False, since=60))
        self.assertTrue(self.d(40.0, True, since=60) is True)

    def test_min_cycle_elapsed_allows_change(self):
        self.assertTrue(self.d(50.0, False, since=301))

    def test_sensor_failure_holds_state(self):
        self.assertTrue(self.d(None, True))
        self.assertFalse(self.d(None, False))
        self.assertIsNone(self.d(None, None))

    def test_unknown_state_uses_plain_thermostat(self):
        self.assertTrue(self.d(45.0, None))
        self.assertFalse(self.d(44.9, None))


if __name__ == '__main__':
    unittest.main()
