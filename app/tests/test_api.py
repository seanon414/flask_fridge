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

    # --- coast minutes ---

    def settings(self):
        return self.client.get('/api/status').get_json()['settings']

    def test_coast_is_off_by_default(self):
        self.assertEqual(self.settings()['coast_minutes'], 0)

    def test_coast_can_be_set_with_the_other_settings(self):
        r = self.client.post('/api/settings',
                             json={'target_temp': 39, 'hysteresis': 0.5, 'coast_minutes': 18})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['coast_minutes'], 18)
        self.assertEqual(self.settings()['coast_minutes'], 18)

    def test_leaving_coast_out_keeps_it(self):
        self.client.post('/api/settings', json={'target_temp': 39, 'hysteresis': 0.5, 'coast_minutes': 18})
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 0.5})
        self.assertEqual(self.settings()['coast_minutes'], 18)
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 0.5, 'coast_minutes': None})
        self.assertEqual(self.settings()['coast_minutes'], 18)

    def test_coast_zero_turns_it_off(self):
        self.client.post('/api/settings', json={'target_temp': 39, 'hysteresis': 0.5, 'coast_minutes': 18})
        self.client.post('/api/settings', json={'target_temp': 39, 'hysteresis': 0.5, 'coast_minutes': 0})
        self.assertEqual(self.settings()['coast_minutes'], 0)

    def test_bad_coast_values_are_rejected_and_change_nothing(self):
        for bad in (-1, 61, 'soon', [5]):
            r = self.client.post('/api/settings',
                                 json={'target_temp': 40, 'hysteresis': 1.0, 'coast_minutes': bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.settings()['target_temp'], 45.0)      # nothing was applied
        self.assertEqual(self.settings()['coast_minutes'], 0)

    def test_changing_coast_logs_an_event(self):
        self.client.post('/api/settings', json={'target_temp': 45, 'hysteresis': 1.0, 'coast_minutes': 18})
        [event] = self.events()
        self.assertEqual(event['kind'], 'settings')
        self.assertIn('Coast 0.0 → 18.0 min', event['detail'])
        self.assertNotIn('Target', event['detail'])

    # --- mode ---

    def test_update_mode(self):
        for mode in ('on', 'off', 'auto'):
            r = self.client.post('/api/mode', json={'mode': mode})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()['mode'], mode)

    def test_update_mode_rejects_unknown(self):
        self.assertEqual(self.client.post('/api/mode', json={'mode': 'turbo'}).status_code, 400)

    # --- half-degree targets ---

    def test_half_degree_targets_are_accepted_and_stored_exactly(self):
        r = self.client.post('/api/settings', json={'target_temp': 38.5, 'hysteresis': 1.0})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get('/api/status').get_json()['settings']['target_temp'], 38.5)

    def test_other_fractions_are_rejected(self):
        for target in (38.3, 38.25, 38.75, 38.01):
            r = self.client.post('/api/settings', json={'target_temp': target, 'hysteresis': 1.0})
            self.assertEqual(r.status_code, 400, target)

    # --- events ---

    def events(self, hours=24):
        return self.client.get('/api/history?hours={}'.format(hours)).get_json()['events']

    def test_changing_settings_logs_an_event(self):
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 1.5})
        [event] = self.events()
        self.assertEqual(event['kind'], 'settings')
        self.assertIn('Target 45.0 → 40.0°F', event['detail'])
        self.assertIn('Swing ±1.0 → ±1.5°F', event['detail'])
        self.assertTrue(event['t'].endswith('Z'))

    def test_only_the_changed_setting_is_described(self):
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 1.0})
        [event] = self.events()
        self.assertIn('Target', event['detail'])
        self.assertNotIn('Swing', event['detail'])

    def test_resaving_the_same_settings_logs_nothing(self):
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 1.5})
        self.client.post('/api/settings', json={'target_temp': 40, 'hysteresis': 1.5})
        self.assertEqual(len(self.events()), 1)

    def test_rejected_settings_log_nothing(self):
        self.client.post('/api/settings', json={'target_temp': 38.3, 'hysteresis': 1.0})
        self.assertEqual(self.events(), [])

    def test_changing_mode_logs_an_event(self):
        self.client.post('/api/mode', json={'mode': 'on'})
        self.client.post('/api/mode', json={'mode': 'auto'})
        details = [e['detail'] for e in self.events()]
        self.assertEqual(details, ['Auto → Forced on', 'Forced on → Auto'])
        self.assertEqual({e['kind'] for e in self.events()}, {'mode'})

    def test_selecting_the_current_mode_logs_nothing(self):
        self.client.post('/api/mode', json={'mode': 'auto'})
        self.assertEqual(self.events(), [])

    def test_history_only_returns_events_inside_the_window(self):
        from app.models.temp import Event
        db.session.add(Event(timestamp=datetime.utcnow() - timedelta(hours=30), kind='mode', detail='old'))
        db.session.add(Event(timestamp=datetime.utcnow() - timedelta(hours=1), kind='mode', detail='recent'))
        db.session.commit()
        self.assertEqual([e['detail'] for e in self.events(24)], ['recent'])
        self.assertEqual([e['detail'] for e in self.events(48)], ['old', 'recent'])

    # --- alerts in status ---

    def test_status_lists_only_active_alerts(self):
        from app.models.temp import AlertState
        db.session.add(AlertState(key='too_warm', active=True, since=datetime.utcnow(),
                                  message='Too warm: 44.0°F'))
        db.session.add(AlertState(key='sensor', active=False, message=''))
        db.session.commit()
        alerts = self.client.get('/api/status').get_json()['alerts']
        self.assertEqual([a['key'] for a in alerts], ['too_warm'])
        self.assertEqual(alerts[0]['message'], 'Too warm: 44.0°F')
        self.assertTrue(alerts[0]['since'].endswith('Z'))

    def test_status_with_no_alerts(self):
        self.assertEqual(self.client.get('/api/status').get_json()['alerts'], [])

    def test_status_reports_whether_push_alerts_are_configured(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'NTFY_TOPIC': ''}):
            self.assertFalse(self.client.get('/api/status').get_json()['notifications_enabled'])
        with mock.patch.dict(os.environ, {'NTFY_TOPIC': 'my-topic'}):
            data = self.client.get('/api/status').get_json()
        self.assertTrue(data['notifications_enabled'])
        self.assertNotIn('my-topic', str(data))      # the topic is a secret; never expose it

    # --- version (lets the wall display reload itself after an update) ---

    def version(self):
        return self.client.get('/api/status').get_json()['version']

    def test_status_includes_a_stable_version(self):
        first, second = self.version(), self.version()
        self.assertRegex(first, r'^\d+\.\d+$')
        self.assertEqual(first, second)

    def test_version_changes_when_a_static_file_changes(self):
        import os
        import time
        before = self.version()
        probe = os.path.join(app.static_folder, '_version_probe.tmp')
        try:
            with open(probe, 'w') as handle:
                handle.write('x')
            future = time.time() + 3600
            os.utime(probe, (future, future))
            after = self.version()
        finally:
            if os.path.exists(probe):
                os.remove(probe)
        self.assertNotEqual(before, after)
        self.assertEqual(self.version(), before)    # and back once the file is gone

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
