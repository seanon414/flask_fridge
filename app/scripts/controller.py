"""Single control loop for the keezer.

Run on the Pi (replaces the cron-driven lager.py):

    python -m app.scripts.controller

Each cycle it reads the sensor, reads the shared settings from the database,
decides whether the fridge should be on, sends the RF command if needed, and
logs a Reading. The web app only edits settings; it never talks to the RF
transmitter, so there is exactly one writer to the hardware.
"""
import logging
import signal
import threading
import time
from datetime import datetime, timedelta

from app import app, db
from app.models.temp import Event, Reading, get_settings
from app.scripts import alerts
from app.scripts.control_logic import cooling_rate, decide
from app.scripts.fridge_control import set_fridge
from app.scripts.thermometer import read_temp

logger = logging.getLogger(__name__)

POLL_SECONDS = 30
RESEND_SECONDS = 600       # re-send the current state in case an RF packet was missed
RETENTION_DAYS = 90
PRUNE_EVERY_SECONDS = 3600
RATE_WINDOW_SECONDS = 360  # how far back the cooling rate looks; older samples are dropped


class Controller(object):
    def __init__(self, sensor=read_temp, switch=set_fridge, clock=time.time, alert_manager=None):
        self._sensor = sensor
        self._switch = switch
        self._clock = clock
        self.alerts = alert_manager or alerts.AlertManager(alerts.Notifier())
        self._lock = threading.Lock()   # guards the state below
        self.fridge_on = None           # None = unknown
        self.last_change = None
        self.last_sent = None
        self.last_prune = None
        self.mode = None                # last mode seen, to log changes
        self._recent = []               # (clock seconds, temp_f), newest last, for the cooling rate
        self._off_temp = None           # temperature when the fridge last turned off ...
        self._off_low = None            # ... and the lowest it has been since

    def _restore_state(self):
        """After a restart, recover the last known state from the log."""
        last = Reading.query.filter(Reading.fridge_on.isnot(None)) \
            .order_by(Reading.timestamp.desc()).first()
        if last is not None:
            self.fridge_on = last.fridge_on

    def _prune(self, now):
        if self.last_prune is not None and now - self.last_prune < PRUNE_EVERY_SECONDS:
            return
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        Reading.query.filter(Reading.timestamp < cutoff).delete()
        Event.query.filter(Event.timestamp < cutoff).delete()
        db.session.commit()
        self.last_prune = now

    def _track_coast(self, now_on, temp_f):
        """Log how far the temperature kept falling after each stop.

        `python -m app.scripts.coast` reads the history to suggest a coast
        setting; this line is the same measurement, live, in the journal.
        """
        if temp_f is None:
            self._off_temp = self._off_low = None
        elif not now_on:
            self._off_temp = self._off_low = temp_f
        elif self._off_temp is not None:
            logger.info('Coast: stopped at %.2fF, bottomed at %.2fF (%.2fF lower) before cooling again',
                        self._off_temp, self._off_low, self._off_temp - self._off_low)
            self._off_temp = self._off_low = None

    def _check_alerts(self, settings):
        """Alerts must never be able to break temperature control."""
        try:
            self.alerts.update_many(alerts.evaluate(settings))
        except Exception:
            logger.exception('Alert check failed')
            db.session.rollback()

    def step(self):
        """Run one control cycle. Must be called inside an app context."""
        with self._lock:
            now = self._clock()
            settings = get_settings()
            if self.fridge_on is None and self.last_sent is None:
                self._restore_state()

            _, temp_f = self._sensor()
            if temp_f is None:
                logger.warning('No valid temperature reading; holding state')
            else:
                self._recent.append((now, temp_f))
            self._recent = [s for s in self._recent if s[0] >= now - RATE_WINDOW_SECONDS]
            rate = cooling_rate(self._recent, window_seconds=RATE_WINDOW_SECONDS)
            if temp_f is not None and self.fridge_on is False and self._off_low is not None:
                self._off_low = min(self._off_low, temp_f)

            if settings.mode != self.mode:
                logger.info('Mode %s -> %s (target=%s hysteresis=%s)',
                            self.mode, settings.mode, settings.target_temp,
                            settings.hysteresis)
                self.mode = settings.mode

            if settings.mode == 'on':
                desired = True
            elif settings.mode == 'off':
                desired = False
            else:
                since = None if self.last_change is None else now - self.last_change
                desired = decide(temp_f, settings.target_temp, settings.hysteresis,
                                 self.fridge_on, since, settings.min_cycle_seconds,
                                 coast_minutes=settings.coast_minutes or 0.0,
                                 cooling_rate_f_per_min=rate)

            if desired is not None:
                changed = desired != self.fridge_on
                stale = self.last_sent is None or now - self.last_sent >= RESEND_SECONDS
                if changed or stale:
                    if self._switch(desired):
                        self.last_sent = now
                        if changed:
                            self.last_change = now
                            self.fridge_on = desired
                            logger.info('Fridge -> %s (temp=%s target=%s mode=%s cooling=%s F/min coast=%s min)',
                                        'ON' if desired else 'OFF', temp_f,
                                        settings.target_temp, settings.mode,
                                        None if rate is None else round(rate, 3),
                                        settings.coast_minutes or 0)
                            self._track_coast(desired, temp_f)
                    else:
                        logger.error('Failed to send fridge command; will retry next cycle')

            db.session.add(Reading(temp_f=temp_f, target_f=settings.target_temp,
                                   fridge_on=self.fridge_on))
            db.session.commit()
            self._prune(now)
            self._check_alerts(settings)


def run(stop_event=None, poll_seconds=POLL_SECONDS):
    stop_event = stop_event or threading.Event()
    controller = Controller()
    while not stop_event.is_set():
        try:
            with app.app_context():
                controller.step()
        except Exception:
            logger.exception('Control cycle failed')
            db.session.rollback()
        stop_event.wait(poll_seconds)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(stop)
