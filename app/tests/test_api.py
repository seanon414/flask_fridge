"""API tests. Run against an in-memory database only:

    DATABASE_URL=sqlite:// python -m unittest discover -s app/tests -t .

They create and drop tables, so they refuse to run against a real database.
"""
import unittest
from datetime import datetime, timedelta

from app import app, db

IN_MEMORY = app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite://'


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run API tests')
class ApiTests(unittest.TestCase):
    def setUp(self):
        from app.models.temp import Reading, Temp  # noqa: F401 (register models)
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def add_reading(self, age_seconds, temp_f, on, target=38):
        from app.models.temp import Reading
        db.session.add(Reading(
            timestamp=datetime.utcnow() - timedelta(seconds=age_seconds),
            temp_f=temp_f, target_f=target, fridge_on=on))
        db.session.commit()

    # --- status ---

    def test_status_with_no_readings(self):
        data = self.client.get('/api/status').get_json()
        self.assertFalse(data['controller_ok'])
        self.assertFalse(data['sensor_ok'])
        self.assertIsNone(data['temp_f'])
        self.assertEqual(data['settings']['mode'], 'auto')

    def test_status_fresh_reading(self):
        self.add_reading(10, 37.5, True)
        data = self.client.get('/api/status').get_json()
        self.assertTrue(data['controller_ok'])
        self.assertTrue(data['sensor_ok'])
        self.assertEqual(data['temp_f'], 37.5)
        self.assertTrue(data['fridge_on'])
        self.assertTrue(data['reading_at'].endswith('Z'))

    def test_status_stale_reading_marks_controller_down(self):
        self.add_reading(600, 37.5, False)
        self.assertFalse(self.client.get('/api/status').get_json()['controller_ok'])

    def test_status_sensor_failure(self):
        self.add_reading(5, None, False)
        data = self.client.get('/api/status').get_json()
        self.assertTrue(data['controller_ok'])
        self.assertFalse(data['sensor_ok'])

    # --- history ---

    def test_history_downsamples_and_computes_stats(self):
        for i in range(1000):
            self.add_reading(i * 30, 36.0 + (i % 4), i % 2 == 0)
        data = self.client.get('/api/history?hours=24').get_json()
        self.assertLessEqual(len(data['points']), 300)
        self.assertEqual(data['stats']['samples'], 1000)
        self.assertEqual(data['stats']['min'], 36.0)
        self.assertEqual(data['stats']['max'], 39.0)
        self.assertEqual(data['stats']['duty_percent'], 50.0)
        times = [p['t'] for p in data['points']]
        self.assertEqual(times, sorted(times))

    def test_history_respects_window(self):
        self.add_reading(60, 37.0, False)
        self.add_reading(3 * 3600, 40.0, False)
        data = self.client.get('/api/history?hours=1').get_json()
        self.assertEqual(data['stats']['samples'], 1)
        self.assertEqual(data['stats']['max'], 37.0)

    def test_history_rejects_bad_hours(self):
        self.assertEqual(self.client.get('/api/history?hours=abc').status_code, 400)

    def test_history_empty(self):
        data = self.client.get('/api/history').get_json()
        self.assertEqual(data['points'], [])
        self.assertIsNone(data['stats']['min'])

    # --- settings ---

    def test_update_settings(self):
        r = self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 1.5})
        self.assertEqual(r.status_code, 200)
        data = self.client.get('/api/status').get_json()
        self.assertEqual(data['settings']['target_temp'], 40)
        self.assertEqual(data['settings']['hysteresis'], 1.5)

    def test_update_settings_validation(self):
        bad = [
            {},
            {'target_temp': 'x', 'hysteresis': 1},
            {'target_temp': 10, 'hysteresis': 1},
            {'target_temp': 80, 'hysteresis': 1},
            {'target_temp': 40, 'hysteresis': 0},
            {'target_temp': 40, 'hysteresis': 50},
        ]
        for body in bad:
            r = self.client.post('/api/settings', json=body)
            self.assertEqual(r.status_code, 400, body)

    # --- mode ---

    def test_update_mode(self):
        for mode in ('on', 'off', 'auto'):
            r = self.client.post('/api/mode', json={'mode': mode})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()['mode'], mode)

    def test_update_mode_rejects_unknown(self):
        self.assertEqual(self.client.post('/api/mode', json={'mode': 'turbo'}).status_code, 400)

    # --- csrf / page ---

    def test_post_requires_csrf_token_when_enabled(self):
        app.config['WTF_CSRF_ENABLED'] = True
        r = self.client.post('/api/mode', json={'mode': 'on'})
        self.assertEqual(r.status_code, 400)

    def test_index_renders_with_csrf_meta(self):
        app.config['WTF_CSRF_ENABLED'] = True
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'name="csrf-token"', r.data)


if __name__ == '__main__':
    unittest.main()
