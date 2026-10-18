"""Coast study and setting. The database tests run against an in-memory database only:

    DATABASE_URL=sqlite:// python -m unittest discover -s app/tests -t .
"""
import unittest
from datetime import datetime, timedelta

from app import app, db
from app.scripts.coast import find_stops, suggest

IN_MEMORY = app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite://'
STEP = 30


def cycle(start=0, rate=0.09, coast=1.62, on_seconds=600, top=40.0, rise_seconds=3600):
    """One compressor cycle as (seconds, temp_f, fridge_on) rows, every 30 s.

    Cooling at `rate` F/min while on, then the temperature keeps falling by
    `coast` degrees over an hour after the stop, then rises until it starts again.
    """
    rows = []
    t = start
    while t < start + on_seconds:
        rows.append((t, top - rate * (t - start) / 60.0, True))
        t += STEP
    stop = top - rate * on_seconds / 60.0
    stop_t = t
    off_end = stop_t + 3600
    while t < off_end:
        rows.append((t, stop - coast * (t - stop_t) / 3600.0, False))
        t += STEP
    bottom = stop - coast
    rise_end = t + rise_seconds
    while t < rise_end:
        rows.append((t, bottom + 1.0 * (t - off_end) / rise_seconds, False))
        t += STEP
    rows.append((t, top, True))          # the next start
    return rows


class FindStopsTests(unittest.TestCase):
    def test_measures_how_far_it_coasted_and_for_how_long(self):
        stops = find_stops(cycle(coast=1.62, rate=0.09))
        self.assertEqual(len(stops), 1)
        stop = stops[0]
        self.assertAlmostEqual(stop['rate'], 0.09, places=3)
        self.assertAlmostEqual(stop['undershoot'], 1.62, places=2)
        self.assertAlmostEqual(stop['coast_minutes'], 18.0, delta=0.2)

    def test_a_faster_fall_with_the_same_coast_means_a_shorter_coast_time(self):
        stop = find_stops(cycle(rate=0.18, coast=1.62, on_seconds=300))[0]
        self.assertAlmostEqual(stop['coast_minutes'], 9.0, delta=0.2)

    def test_skips_a_cycle_with_a_gap_in_the_readings(self):
        rows = cycle()
        stop_index = next(i for i, r in enumerate(rows) if r[2] is False)
        # the controller was down for ten minutes while it coasted
        gapped = rows[:stop_index + 20] + rows[stop_index + 40:]
        self.assertEqual(find_stops(gapped), [])

    def test_skips_a_stop_whose_bottom_has_not_happened_yet(self):
        rows = cycle()
        self.assertEqual(find_stops(rows[:-1]), [])        # data ends before it starts again

    def test_a_slow_fall_is_recorded_but_not_used(self):
        stops = find_stops(cycle(rate=0.01, coast=0.3))
        self.assertEqual(len(stops), 1)
        self.assertIsNone(stops[0]['coast_minutes'])
        self.assertIsNone(suggest(stops))

    def test_sensor_failures_do_not_crash_it(self):
        rows = cycle()
        rows[5] = (rows[5][0], None, rows[5][2])
        self.assertEqual(len(find_stops(rows)), 1)

    def test_several_cycles(self):
        rows = cycle(coast=1.62)
        rows += cycle(start=rows[-1][0] + STEP, coast=1.62)
        self.assertEqual(len(find_stops(rows)), 2)


class SuggestTests(unittest.TestCase):
    def test_takes_the_median(self):
        stops = [{'coast_minutes': v} for v in (10.0, 30.0, 18.0)]
        self.assertEqual(suggest(stops), 18.0)

    def test_ignores_unmeasurable_stops(self):
        self.assertEqual(suggest([{'coast_minutes': None}, {'coast_minutes': 12.0}]), 12.0)

    def test_nothing_to_suggest(self):
        self.assertIsNone(suggest([]))


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run these tests')
class CoastCommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def lines(self):
        out = []
        return out, out.append

    def test_set_changes_the_setting_and_logs_an_event(self):
        from app.models.temp import Event, get_settings
        from app.scripts.coast import set_coast
        out, say = self.lines()
        set_coast(18, out=say)
        self.assertEqual(get_settings().coast_minutes, 18.0)
        self.assertEqual(Event.query.count(), 1)
        self.assertIn('Coast 0.0', Event.query.one().detail)

    def test_setting_the_same_value_again_logs_nothing(self):
        from app.models.temp import Event
        from app.scripts.coast import set_coast
        set_coast(18, out=lambda *_: None)
        set_coast(18, out=lambda *_: None)
        self.assertEqual(Event.query.count(), 1)

    def test_zero_turns_it_off(self):
        from app.models.temp import get_settings
        from app.scripts.coast import set_coast
        set_coast(18, out=lambda *_: None)
        set_coast(0, out=lambda *_: None)
        self.assertEqual(get_settings().coast_minutes, 0.0)

    def test_rejects_values_out_of_range(self):
        from app.models.temp import get_settings
        from app.scripts.coast import set_coast
        for bad in (-1, 61, 500):
            with self.assertRaises(ValueError):
                set_coast(bad, out=lambda *_: None)
        self.assertEqual(get_settings().coast_minutes, 0.0)

    def test_new_settings_default_to_off(self):
        from app.models.temp import get_settings
        self.assertEqual(get_settings().coast_minutes, 0.0)

    def test_analyze_reads_the_readings_table_and_suggests_a_value(self):
        from app.models.temp import Reading
        from app.scripts.coast import analyze
        base = datetime.utcnow() - timedelta(hours=8)
        for t, temp, on in cycle(coast=1.62, rate=0.09):
            db.session.add(Reading(timestamp=base + timedelta(seconds=t), temp_f=temp,
                                   target_f=39.0, fridge_on=on))
        db.session.commit()

        out, say = self.lines()
        value = analyze(1, out=say)
        self.assertAlmostEqual(value, 18.0, delta=0.2)
        text = '\n'.join(out)
        self.assertIn('1 complete compressor stops', text)
        self.assertIn('Suggested: python -m app.scripts.coast set 18', text)
        self.assertIn('rough guess', text)             # one cycle is not a measurement

    def test_analyze_with_no_history_says_so(self):
        from app.scripts.coast import analyze
        out, say = self.lines()
        self.assertIsNone(analyze(3, out=say))
        self.assertIn('Not enough complete cycles', '\n'.join(out))


if __name__ == '__main__':
    unittest.main()
