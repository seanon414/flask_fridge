import unittest

from app.scripts.control_logic import MAX_ANTICIPATION_F, cooling_rate, decide


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


def line(rate_per_min, start=40.0, seconds=360, step=30):
    """Samples falling at rate_per_min degrees F per minute."""
    return [(t, start - rate_per_min * t / 60.0) for t in range(0, seconds + 1, step)]


class CoolingRateTests(unittest.TestCase):
    def test_measures_a_steady_fall(self):
        self.assertAlmostEqual(cooling_rate(line(0.09)), 0.09, places=4)

    def test_a_rising_temperature_is_negative(self):
        self.assertAlmostEqual(cooling_rate(line(-0.05)), -0.05, places=4)

    def test_flat_is_zero(self):
        self.assertAlmostEqual(cooling_rate(line(0.0)), 0.0, places=6)

    def test_only_the_recent_window_counts(self):
        old_rise = [(t, 38.0 + 0.1 * t / 60.0) for t in range(0, 600, 30)]
        recent_fall = [(600 + t, 39.0 - 0.09 * t / 60.0) for t in range(0, 361, 30)]
        self.assertAlmostEqual(cooling_rate(old_rise + recent_fall), 0.09, places=4)

    def test_smooths_the_sensors_coarse_steps(self):
        # real data: steps of 0.11F, falling about 0.09F per minute on average
        stepped = [(t, round((40.0 - 0.09 * t / 60.0) / 0.1125) * 0.1125) for t in range(0, 361, 30)]
        self.assertAlmostEqual(cooling_rate(stepped), 0.09, delta=0.03)

    def test_not_enough_data_is_none(self):
        self.assertIsNone(cooling_rate([]))
        self.assertIsNone(cooling_rate(line(0.09, seconds=60)))           # too short a span
        self.assertIsNone(cooling_rate([(0, 40.0), (200, 39.8), (400, 39.6)]))   # too few points

    def test_identical_timestamps_do_not_divide_by_zero(self):
        self.assertIsNone(cooling_rate([(5, 40.0)] * 6))


class CoastAwareDecideTests(unittest.TestCase):
    """target 39, swing 0.5: normally off at 38.5 and below."""

    def d(self, temp, is_on=True, rate=0.09, coast=18, since=9999, minimum=300):
        return decide(temp, 39.0, 0.5, is_on, since, minimum,
                      coast_minutes=coast, cooling_rate_f_per_min=rate)

    def test_off_by_default_acts_like_a_plain_thermostat(self):
        self.assertTrue(decide(38.9, 39.0, 0.5, True, 9999, 300))
        self.assertTrue(decide(38.9, 39.0, 0.5, True, 9999, 300, coast_minutes=0, cooling_rate_f_per_min=0.09))

    def test_stops_early_while_the_temperature_is_still_falling(self):
        # 0.09F/min * 18 min = 1.62F of expected coast: stop as soon as temp - 1.62 <= 38.5
        self.assertFalse(self.d(40.1))
        self.assertTrue(self.d(40.2))

    def test_a_slower_fall_means_less_anticipation(self):
        self.assertFalse(self.d(39.0, rate=0.04))      # 39.0 - 0.72 = 38.28 <= 38.5
        self.assertTrue(self.d(39.3, rate=0.04))       # 39.3 - 0.72 = 38.58 > 38.5

    def test_no_anticipation_without_a_rate(self):
        self.assertTrue(self.d(38.9, rate=None))
        self.assertFalse(self.d(38.4, rate=None))

    def test_no_anticipation_when_not_cooling(self):
        self.assertTrue(self.d(38.9, rate=0.0))
        self.assertTrue(self.d(38.9, rate=-0.05))

    def test_anticipation_is_capped(self):
        # a wild rate reading must not switch the compressor off far from the band
        self.assertEqual(MAX_ANTICIPATION_F, 2.0)
        self.assertTrue(self.d(41.0, rate=5.0))
        self.assertFalse(self.d(40.5, rate=5.0))

    def test_the_minimum_cycle_still_holds_the_compressor_on(self):
        self.assertTrue(self.d(38.0, since=60))
        self.assertFalse(self.d(38.0, since=301))

    def test_it_never_changes_when_turning_on(self):
        self.assertFalse(self.d(39.4, is_on=False))
        self.assertTrue(self.d(39.5, is_on=False))

    def test_sensor_failure_still_holds_state(self):
        self.assertTrue(self.d(None))
        self.assertFalse(self.d(None, is_on=False))


if __name__ == '__main__':
    unittest.main()
