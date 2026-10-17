"""Push notifications for the keezer, via ntfy (https://ntfy.sh).

Off unless NTFY_TOPIC is set (put it in .env; treat it like a password, since
anyone who knows the topic name can read your alerts). Settings, all optional:

    NTFY_TOPIC            topic name; alerts are disabled when unset
    NTFY_SERVER           default https://ntfy.sh (set it if you self-host)
    ALERT_WARM_MARGIN     degF above the band before "too warm" counts (default 3)
    ALERT_COLD_MARGIN     degF below the band before "too cold" counts (default 3)
    ALERT_MINUTES         how long a temperature problem must last (default 30)
    ALERT_SENSOR_MINUTES  how long the sensor must be failing (default 10)
    ALERT_CONTROLLER_MINUTES  silence before "controller down" (default 5)
    ALERT_REMIND_HOURS    re-send a still-active alert after this long (default 6)

Send yourself a test notification with:  python -m app.scripts.alerts test
"""
import logging
import os
import sys
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Per-alert notification title, ntfy priority and tag. Titles must be plain ASCII
# because they travel in an HTTP header.
ALERTS = {
    'too_warm': ('Keezer too warm', 'high', 'warning'),
    'too_cold': ('Keezer too cold', 'high', 'snowflake'),
    'sensor': ('Keezer sensor failure', 'high', 'warning'),
    'controller': ('Keezer controller down', 'urgent', 'rotating_light'),
}

Condition = namedtuple('Condition', 'active message')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


class Notifier(object):
    def __init__(self, topic=None, server=None, opener=None):
        self.topic = topic if topic is not None else os.environ.get('NTFY_TOPIC', '').strip()
        self.server = (server or os.environ.get('NTFY_SERVER') or 'https://ntfy.sh').rstrip('/')
        self._open = opener or urllib.request.urlopen

    @property
    def enabled(self):
        return bool(self.topic)

    def send(self, title, message, priority='default', tags=''):
        """Returns True if the server accepted it. Never raises."""
        if not self.enabled:
            return False
        request = urllib.request.Request(
            '{}/{}'.format(self.server, self.topic),
            data=message.encode('utf-8'), method='POST')
        request.add_header('Title', title)
        request.add_header('Priority', priority)
        if tags:
            request.add_header('Tags', tags)
        try:
            with self._open(request, timeout=5) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError):
            logger.exception('Could not send notification')
            return False


class AlertManager(object):
    """Turns "condition is true right now" into "tell me once, then remind me".

    State lives in the alert_state table, so it survives restarts and is shared
    between the controller and the watchdog.
    """
    RETRY_SECONDS = 300      # don't hammer ntfy while it is unreachable

    def __init__(self, notifier, remind_hours=None):
        self.notifier = notifier
        hours = remind_hours if remind_hours is not None else _env_float('ALERT_REMIND_HOURS', 6)
        self.remind = timedelta(hours=hours)
        self._attempts = {}

    def update_many(self, conditions, now=None):
        for key, condition in conditions.items():
            self.update(key, condition.active, condition.message, now)

    def update(self, key, active, message, now=None):
        from app import db
        from app.models.temp import AlertState

        now = now or datetime.utcnow()
        row = db.session.get(AlertState, key)
        if row is None:
            row = AlertState(key=key, active=False, message='')
            db.session.add(row)

        if active:
            if not row.active:
                row.active = True
                row.since = now
                row.last_notified = None
            row.message = message
            due = row.last_notified is None or now - row.last_notified >= self.remind
            if due and self.notifier.enabled and self._may_attempt(key, now):
                if self._notify(key, message):
                    row.last_notified = now
        elif row.active:
            told_you = row.last_notified is not None
            row.active = False
            row.since = None
            row.last_notified = None
            row.message = ''
            if told_you:
                title, _, _ = ALERTS[key]
                self.notifier.send(title + ': resolved', 'Back to normal.', 'default', 'white_check_mark')
        db.session.commit()

    def _may_attempt(self, key, now):
        last = self._attempts.get(key)
        if last is not None and (now - last).total_seconds() < self.RETRY_SECONDS:
            return False
        self._attempts[key] = now
        return True

    def _notify(self, key, message):
        title, priority, tags = ALERTS[key]
        return self.notifier.send(title, message, priority, tags)


def _sustained(rows, now, minutes, predicate):
    """True if every reading in the last `minutes` matches, and we actually
    have readings covering that whole stretch (not just the last few seconds)."""
    start = now - timedelta(minutes=minutes)
    window = [r for r in rows if r.timestamp >= start]
    if not window:
        return False
    if window[0].timestamp > start + timedelta(minutes=2):
        return False       # not enough history yet (e.g. just restarted)
    return all(predicate(r) for r in window)


def evaluate(settings, now=None):
    """Work out which alert conditions hold right now, from the reading log."""
    from app.models.temp import Reading

    now = now or datetime.utcnow()
    warm_margin = _env_float('ALERT_WARM_MARGIN', 3)
    cold_margin = _env_float('ALERT_COLD_MARGIN', 3)
    minutes = _env_float('ALERT_MINUTES', 30)
    sensor_minutes = _env_float('ALERT_SENSOR_MINUTES', 10)

    longest = max(minutes, sensor_minutes)
    rows = Reading.query.filter(Reading.timestamp >= now - timedelta(minutes=longest + 5)) \
        .order_by(Reading.timestamp).all()

    high = settings.target_temp + settings.hysteresis + warm_margin
    low = settings.target_temp - settings.hysteresis - cold_margin
    latest_temp = next((r.temp_f for r in reversed(rows) if r.temp_f is not None), None)

    warm = _sustained(rows, now, minutes, lambda r: r.temp_f is not None and r.temp_f > high)
    cold = _sustained(rows, now, minutes, lambda r: r.temp_f is not None and r.temp_f < low)
    sensor = _sustained(rows, now, sensor_minutes, lambda r: r.temp_f is None)

    shown = '{:.1f}'.format(latest_temp) if latest_temp is not None else '--'
    return {
        'too_warm': Condition(warm, 'Too warm: {}°F for {:.0f}+ min (alert above {:.1f}°F).'.format(
            shown, minutes, high)),
        'too_cold': Condition(cold, 'Too cold: {}°F for {:.0f}+ min (alert below {:.1f}°F).'.format(
            shown, minutes, low)),
        'sensor': Condition(sensor, 'The temperature sensor has returned no valid reading for {:.0f}+ min.'.format(
            sensor_minutes)),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    notifier = Notifier()
    if sys.argv[1:] != ['test']:
        print('usage: python -m app.scripts.alerts test')
        sys.exit(2)
    if not notifier.enabled:
        print('NTFY_TOPIC is not set; add it to .env (and export it in this shell) first.')
        sys.exit(1)
    ok = notifier.send('Keezer test', 'If you can read this, alerts work.', 'default', 'white_check_mark')
    print('sent' if ok else 'FAILED to send (see log above)')
    sys.exit(0 if ok else 1)
