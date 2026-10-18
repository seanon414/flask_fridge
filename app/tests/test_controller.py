"""Controller tests with a fake sensor, RF switch and clock.

Run against an in-memory database only:

    DATABASE_URL=sqlite:// python -m unittest discover -s app/tests -t .
"""
import unittest
from datetime import datetime, timedelta

from app import app, db

IN_MEMORY = app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite://'


class FakeSwitch(object):
    """Stands in for the RF transmitter and records every command sent."""
    def __init__(self):
        self.calls = []
        self.ok = True

    def __call__(self, on):
        self.calls.append(on)
        return self.ok


class FakeAlerts(object):
    """Stands in for AlertManager so no test can ever send a notification."""
    def __init__(self):
        self.calls = []
        self.fail = False

    def update_many(self, conditions, now=None):
        if self.fail:
            raise RuntimeError('alerts are broken')
        self.calls.append(conditions)


@unittest.skipUnless(IN_MEMORY, 'set DATABASE_URL=sqlite:// to run controller tests')
class ControllerTests(unittest.TestCase):
    def setUp(self):
        from app.models.temp import Reading, Temp, get_settings  # noqa: F401
        from app.scripts.controller import Controller
        self.Controller = Controller
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        self.now = 1000.0
        self.temp = 38.0
        self.switch = FakeSwitch()
        self.alerts = FakeAlerts()
        self.controller = self.make_controller()

        settings = get_settings()
        settings.target_temp = 38
        settings.hysteresis = 1.0
        settings.min_cycle_seconds = 300
        settings.mode = 'auto'
        db.session.commit()
        self.settings = settings

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_controller(self):
        def sensor():
            if self.temp is None:
                return None, None
            return (self.temp - 32) / 1.8, self.temp
        return self.Controller(sensor=sensor, switch=self.switch, clock=lambda: self.now,
                               alert_manager=self.alerts)

    def step(self, temp=None, advance=0):
        """Advance the clock, optionally change the temperature, run one cycle."""
        self.now += advance
        if temp is not None:
            self.temp = temp
        self.controller.step()

    def last_reading(self):
        from app.models.temp import Reading
        return Reading.query.order_by(Reading.id.desc()).first()

    # --- first cycle ---

    def test_first_cycle_below_target_turns_off(self):
        self.step(temp=36.0)
        self.assertEqual(self.switch.calls, [False])
        self.assertIs(self.controller.fridge_on, False)

    def test_first_cycle_above_target_turns_on(self):
        self.step(temp=41.0)
        self.assertEqual(self.switch.calls, [True])
        self.assertIs(self.controller.fridge_on, True)

    # --- hysteresis and compressor protection ---

    def test_holds_state_inside_band(self):
        self.step(temp=36.0)                    # off
        self.step(temp=38.9, advance=400)       # inside band
        self.assertEqual(self.switch.calls, [False])

    def test_min_cycle_blocks_then_allows_turn_on(self):
        self.step(temp=36.0)                    # off at t=1000
        self.step(temp=40.0, advance=100)       # too soon
        self.assertEqual(self.switch.calls, [False])
        self.step(advance=201)                  # 301s since the change
        self.assertEqual(self.switch.calls, [False, True])
        self.assertIs(self.controller.fridge_on, True)

    def test_turns_off_below_band_after_min_cycle(self):
        self.step(temp=41.0)                    # on
        self.step(temp=36.9, advance=301)
        self.assertEqual(self.switch.calls, [True, False])

    def test_stays_on_inside_band(self):
        self.step(temp=41.0)                    # on
        self.step(temp=37.5, advance=301)       # above the 37.0 off point
        self.assertEqual(self.switch.calls, [True])

    # --- manual modes ---

    def test_forced_on_ignores_temperature(self):
        self.settings.mode = 'on'
        db.session.commit()
        self.step(temp=30.0)
        self.assertEqual(self.switch.calls, [True])

    def test_forced_modes_bypass_min_cycle(self):
        self.settings.mode = 'on'
        db.session.commit()
        self.step(temp=30.0)
        self.settings.mode = 'off'
        db.session.commit()
        self.step()                             # no time passed
        self.assertEqual(self.switch.calls, [True, False])

    def test_back_to_auto_resumes_thermostat(self):
        self.settings.mode = 'on'
        db.session.commit()
        self.step(temp=30.0)
        self.settings.mode = 'auto'
        db.session.commit()
        self.step(advance=301)                  # 30F is below the band
        self.assertEqual(self.switch.calls, [True, False])

    # --- sensor failure ---

    def test_sensor_failure_holds_state(self):
        self.step(temp=36.0)                    # off
        self.temp = None
        self.step(advance=100)
        self.assertEqual(self.switch.calls, [False])
        reading = self.last_reading()
        self.assertIsNone(reading.temp_f)
        self.assertIs(reading.fridge_on, False)

    def test_sensor_failure_at_startup_sends_nothing(self):
        self.temp = None
        self.step()
        self.assertEqual(self.switch.calls, [])
        self.assertIsNone(self.controller.fridge_on)
        self.assertIsNone(self.last_reading().fridge_on)

    # --- RF reliability ---

    def test_failed_send_is_retried_and_state_not_committed(self):
        self.switch.ok = False
        self.step(temp=36.0)
        self.assertIsNone(self.controller.fridge_on)
        self.switch.ok = True
        self.step(advance=30)
        self.assertEqual(self.switch.calls, [False, False])
        self.assertIs(self.controller.fridge_on, False)

    def test_resends_state_periodically(self):
        from app.scripts.controller import RESEND_SECONDS
        self.step(temp=36.0)
        self.step(advance=RESEND_SECONDS - 1)
        self.assertEqual(self.switch.calls, [False])
        self.step(advance=2)
        self.assertEqual(self.switch.calls, [False, False])

    # --- restart behaviour ---

    def test_restores_state_after_restart_and_resyncs_outlet(self):
        from app.models.temp import Reading
        db.session.add(Reading(timestamp=datetime.utcnow() - timedelta(minutes=10),
                               temp_f=39.5, target_f=38, fridge_on=True))
        db.session.commit()
        fresh = self.make_controller()          # simulates a restarted process
        self.controller = fresh
        self.step(temp=38.0)                    # inside the band
        self.assertIs(fresh.fridge_on, True)    # remembered, not reset to off
        self.assertEqual(self.switch.calls, [True])

    # --- logging ---

    def test_logs_a_reading_every_cycle(self):
        from app.models.temp import Reading
        self.step(temp=36.0)
        self.step(temp=36.1, advance=30)
        self.assertEqual(Reading.query.count(), 2)
        last = self.last_reading()
        self.assertAlmostEqual(last.temp_f, 36.1)
        self.assertEqual(last.target_f, 38)

    def test_logs_mode_changes(self):
        with self.assertLogs('app.scripts.controller', level='INFO') as logs:
            self.step(temp=36.0)
            self.settings.mode = 'on'
            db.session.commit()
            self.step(advance=30)
        text = '\n'.join(logs.output)
        self.assertIn('Mode None -> auto', text)
        self.assertIn('Mode auto -> on', text)

    def test_prunes_old_readings_only(self):
        from app.models.temp import Reading
        from app.scripts.controller import RETENTION_DAYS
        db.session.add(Reading(timestamp=datetime.utcnow() - timedelta(days=RETENTION_DAYS + 1),
                               temp_f=40.0, target_f=38, fridge_on=False))
        db.session.add(Reading(timestamp=datetime.utcnow() - timedelta(days=RETENTION_DAYS - 1),
                               temp_f=41.0, target_f=38, fridge_on=False))
        db.session.commit()
        self.step(temp=36.0)
        temps = sorted(r.temp_f for r in Reading.query.all())
        self.assertNotIn(40.0, temps)
        self.assertIn(41.0, temps)

    def test_prunes_old_events_too(self):
        from app.models.temp import Event
        from app.scripts.controller import RETENTION_DAYS
        db.session.add(Event(timestamp=datetime.utcnow() - timedelta(days=RETENTION_DAYS + 1),
                             kind='mode', detail='ancient'))
        db.session.add(Event(timestamp=datetime.utcnow() - timedelta(days=1),
                             kind='mode', detail='yesterday'))
        db.session.commit()
        self.step(temp=36.0)
        self.assertEqual([e.detail for e in Event.query.all()], ['yesterday'])

    # --- alerts ---

    def test_checks_alert_conditions_every_cycle(self):
        self.step(temp=36.0)
        self.step(advance=30)
        self.assertEqual(len(self.alerts.calls), 2)
        self.assertEqual(set(self.alerts.calls[0]), {'too_warm', 'too_cold', 'sensor'})

    def test_a_failing_alert_check_never_stops_temperature_control(self):
        self.alerts.fail = True
        with self.assertLogs('app.scripts.controller', level='ERROR') as logs:
            self.step(temp=41.0)
        self.assertIn('Alert check failed', '\n'.join(logs.output))
        self.assertEqual(self.switch.calls, [True])           # control still happened
        self.assertEqual(self.last_reading().temp_f, 41.0)    # and the reading was logged
        self.alerts.fail = False
        self.step(advance=30)                                  # and the loop keeps working
        self.assertEqual(len(self.alerts.calls), 1)


if __name__ == '__main__':
    unittest.main()
