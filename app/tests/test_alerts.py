"""Alert tests: notifier, alert state machine, conditions, and the watchdog.

Run against an in-memory database only:

    DATABASE_URL=sqlite:// python -m unittest discover -s app/tests -t .

Nothing here can send a real notification: the notifier gets a fake opener,
and NTFY_* / ALERT_* variables are cleared from the environment for each test.
"""
import os
import unittest
import urllib.error
from datetime import datetime, timedelta
from unittest import mock

from app import app, db

IN_MEMORY = app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite://'


class FakeResponse(object):
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener(object):
    def __init__(self, status=200, error=None):
        self.requests = []
        self.status = status
        self.error = error

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return FakeResponse(self.status)


class FakeNotifier(object):
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.ok = True
        self.sent = []

    def send(self, title, message, priority='default', tags=''):
        self.sent.append((title, message, priority))
        return self.ok


class CleanEnvMixin(object):
    def clean_env(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in list(os.environ):
            if key.startswith(('NTFY_', 'ALERT_')):
                del os.environ[key]


class NotifierTests(CleanEnvMixin, unittest.TestCase):
    def setUp(self):
        self.clean_env()
        from app.scripts.alerts import Notifier
        self.Notifier = Notifier

    def test_disabled_without_a_topic(self):
        opener = FakeOpener()
        notifier = self.Notifier(opener=opener)
        self.assertFalse(notifier.enabled)
        self.assertFalse(notifier.send('t', 'm'))
        self.assertEqual(opener.requests, [])

    def test_topic_comes_from_the_environment(self):
        os.environ['NTFY_TOPIC'] = 'my-secret-topic'
        self.assertTrue(self.Notifier().enabled)

    def test_sends_the_expected_request(self):
        opener = FakeOpener()
        notifier = self.Notifier(topic='abc', opener=opener)
        self.assertTrue(notifier.send('Keezer too warm', 'It is 44°F', 'high', 'warning'))
        request = opener.requests[0]
        self.assertEqual(request.full_url, 'https://ntfy.sh/abc')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, 'It is 44°F'.encode('utf-8'))
        self.assertEqual(request.get_header('Title'), 'Keezer too warm')
        self.assertEqual(request.get_header('Priority'), 'high')
        self.assertEqual(request.get_header('Tags'), 'warning')

    def test_custom_server(self):
        opener = FakeOpener()
        self.Notifier(topic='abc', server='http://pi.local:8080/', opener=opener).send('t', 'm')
        self.assertEqual(opener.requests[0].full_url, 'http://pi.local:8080/abc')

    def test_network_errors_return_false_and_do_not_raise(self):
        notifier = self.Notifier(topic='abc', opener=FakeOpener(error=urllib.error.URLError('down')))
        self.assertFalse(notifier.send('t', 'm'))

    def test_unsuccessful_status_returns_false(self):
        notifier = self.Notifier(topic='abc', opener=FakeOpener(status=500))
        self.assertFalse(notifier.send('t', 'm'))


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run alert tests')
class AlertManagerTests(CleanEnvMixin, unittest.TestCase):
    def setUp(self):
        from app.models.temp import AlertState, Reading, Temp  # noqa: F401
        from app.scripts.alerts import AlertManager
        self.clean_env()
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.AlertManager = AlertManager
        self.notifier = FakeNotifier()
        self.manager = AlertManager(self.notifier, remind_hours=6)
        self.now = datetime(2026, 10, 18, 12, 0, 0)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def row(self, key='too_warm'):
        from app.models.temp import AlertState
        return AlertState.query.get(key)

    def test_notifies_once_when_a_condition_starts(self):
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(minutes=1))
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(minutes=30))
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0][0], 'Keezer too warm')
        self.assertEqual(self.notifier.sent[0][2], 'high')
        row = self.row()
        self.assertTrue(row.active)
        self.assertEqual(row.since, self.now)
        self.assertEqual(row.last_notified, self.now)

    def test_reminds_after_the_reminder_interval(self):
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(hours=5, minutes=59))
        self.assertEqual(len(self.notifier.sent), 1)
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(hours=6, minutes=1))
        self.assertEqual(len(self.notifier.sent), 2)

    def test_sends_a_resolved_notice_and_clears_the_row(self):
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', False, '', self.now + timedelta(minutes=45))
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertEqual(self.notifier.sent[1][0], 'Keezer too warm: resolved')
        row = self.row()
        self.assertFalse(row.active)
        self.assertIsNone(row.since)
        self.assertEqual(row.message, '')

    def test_a_new_episode_notifies_again(self):
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', False, '', self.now + timedelta(minutes=10))
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(minutes=20))
        self.assertEqual(len(self.notifier.sent), 3)   # alert, resolved, alert again

    def test_no_resolved_notice_if_the_alert_was_never_delivered(self):
        self.notifier.ok = False
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', False, '', self.now + timedelta(minutes=10))
        self.assertEqual(len(self.notifier.sent), 1)   # the failed attempt only

    def test_disabled_notifier_still_tracks_state_for_the_ui(self):
        self.notifier.enabled = False
        self.manager.update('sensor', True, 'No sensor', self.now)
        self.assertEqual(self.notifier.sent, [])
        self.assertTrue(self.row('sensor').active)
        self.assertEqual(self.row('sensor').message, 'No sensor')

    def test_failed_send_is_retried_but_not_every_cycle(self):
        self.notifier.ok = False
        self.manager.update('too_warm', True, 'Too warm', self.now)
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(seconds=30))
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(seconds=240))
        self.assertEqual(len(self.notifier.sent), 1)
        self.notifier.ok = True
        self.manager.update('too_warm', True, 'Too warm', self.now + timedelta(seconds=301))
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertIsNotNone(self.row().last_notified)

    def test_state_survives_a_restart(self):
        self.manager.update('too_warm', True, 'Too warm', self.now)
        fresh = self.AlertManager(self.notifier, remind_hours=6)   # new process
        fresh.update('too_warm', True, 'Too warm', self.now + timedelta(minutes=5))
        self.assertEqual(len(self.notifier.sent), 1)

    def test_update_many(self):
        from app.scripts.alerts import Condition
        self.manager.update_many({'too_warm': Condition(True, 'warm'), 'sensor': Condition(False, '')}, self.now)
        self.assertTrue(self.row('too_warm').active)
        self.assertFalse(self.row('sensor').active)


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run alert tests')
class EvaluateTests(CleanEnvMixin, unittest.TestCase):
    """target 38, swing 1  ->  warm alert above 42, cold alert below 34."""

    def setUp(self):
        from app.models.temp import Reading, Temp, get_settings  # noqa: F401
        from app.scripts.alerts import evaluate
        self.clean_env()
        self.evaluate = evaluate
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.now = datetime.utcnow()
        settings = get_settings()
        settings.target_temp = 38.0
        settings.hysteresis = 1.0
        db.session.commit()
        self.settings = settings

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def fill(self, minutes, temp, step_seconds=30):
        """One reading every step_seconds for the last `minutes`, oldest first."""
        from app.models.temp import Reading
        count = int(minutes * 60 / step_seconds)
        for i in range(count, -1, -1):
            db.session.add(Reading(timestamp=self.now - timedelta(seconds=i * step_seconds),
                                   temp_f=temp, target_f=38.0, fridge_on=False))
        db.session.commit()

    def active(self):
        return sorted(k for k, c in self.evaluate(self.settings, self.now).items() if c.active)

    def test_nothing_in_a_healthy_band(self):
        self.fill(40, 38.5)
        self.assertEqual(self.active(), [])

    def test_too_warm_after_a_sustained_stretch(self):
        self.fill(35, 44.0)
        self.assertEqual(self.active(), ['too_warm'])
        message = self.evaluate(self.settings, self.now)['too_warm'].message
        self.assertIn('44.0', message)
        self.assertIn('42.0', message)

    def test_too_cold_after_a_sustained_stretch(self):
        self.fill(35, 31.0)
        self.assertEqual(self.active(), ['too_cold'])

    def test_a_brief_excursion_does_not_alert(self):
        self.fill(10, 44.0)           # only 10 minutes of history
        self.assertEqual(self.active(), [])

    def test_a_single_good_reading_in_the_window_cancels_it(self):
        from app.models.temp import Reading
        self.fill(35, 44.0)
        db.session.add(Reading(timestamp=self.now - timedelta(minutes=10), temp_f=40.0,
                               target_f=38.0, fridge_on=True))
        db.session.commit()
        self.assertEqual(self.active(), [])

    def test_just_over_the_band_is_not_an_alert(self):
        self.fill(35, 41.9)           # above the band but inside the 3F margin
        self.assertEqual(self.active(), [])

    def test_sensor_failure(self):
        self.fill(12, None)
        self.assertEqual(self.active(), ['sensor'])

    def test_missing_readings_do_not_count_as_warm_or_cold(self):
        self.fill(35, None)
        self.assertEqual(self.active(), ['sensor'])

    def test_recovering_sensor_clears_the_alert(self):
        self.fill(12, None)
        from app.models.temp import Reading
        db.session.add(Reading(timestamp=self.now, temp_f=38.0, target_f=38.0, fridge_on=False))
        db.session.commit()
        self.assertEqual(self.active(), [])

    def test_thresholds_can_be_tuned_from_the_environment(self):
        os.environ['ALERT_MINUTES'] = '5'
        os.environ['ALERT_WARM_MARGIN'] = '1'
        self.fill(8, 40.5)            # 38 + 1 + 1 = 40 limit
        self.assertEqual(self.active(), ['too_warm'])

    def test_no_readings_at_all(self):
        self.assertEqual(self.active(), [])


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run alert tests')
class WatchdogTests(CleanEnvMixin, unittest.TestCase):
    def setUp(self):
        from app.models.temp import AlertState, Reading, Temp  # noqa: F401
        from app.scripts.alerts import AlertManager
        from app.scripts import watchdog
        self.clean_env()
        self.watchdog = watchdog
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.notifier = FakeNotifier()
        self.manager = AlertManager(self.notifier, remind_hours=6)
        self.now = datetime(2026, 10, 18, 12, 0, 0)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def reading(self, minutes_ago):
        from app.models.temp import Reading
        db.session.add(Reading(timestamp=self.now - timedelta(minutes=minutes_ago),
                               temp_f=38.0, target_f=38.0, fridge_on=False))
        db.session.commit()

    def test_no_readings_means_the_controller_is_down(self):
        self.assertTrue(self.watchdog.check(self.manager, self.now))
        self.assertEqual(self.notifier.sent[0][0], 'Keezer controller down')
        self.assertEqual(self.notifier.sent[0][2], 'urgent')

    def test_recent_reading_is_fine(self):
        self.reading(1)
        self.assertFalse(self.watchdog.check(self.manager, self.now))
        self.assertEqual(self.notifier.sent, [])

    def test_silence_beyond_the_limit_alerts_once(self):
        self.reading(6)
        self.assertTrue(self.watchdog.check(self.manager, self.now))
        self.assertTrue(self.watchdog.check(self.manager, self.now + timedelta(minutes=2)))
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn('min', self.notifier.sent[0][1])

    def test_recovery_sends_a_resolved_notice(self):
        self.reading(6)
        self.watchdog.check(self.manager, self.now)
        self.reading(0)                                   # the controller is back
        self.assertFalse(self.watchdog.check(self.manager, self.now))
        self.assertEqual(self.notifier.sent[-1][0], 'Keezer controller down: resolved')

    def test_limit_is_configurable(self):
        self.reading(3)
        self.assertFalse(self.watchdog.check(self.manager, self.now, minutes=5))
        self.assertTrue(self.watchdog.check(self.manager, self.now, minutes=2))


if __name__ == '__main__':
    unittest.main()
